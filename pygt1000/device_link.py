"""The request/response conversation with the unit.

``DeviceLink`` sits on top of the :class:`~pygt1000.transport.Transport` seam
and owns the "send a request, block for the matching reply, route everything
else to a handler" mechanism that used to be smeared across
``GT1000.send_message`` / ``fetch_mem`` / ``wait_recv_data`` /
``process_received_message``, backed by the lock-guarded ``received_data`` dict
and ``data_semaphore``.

The old correlation dict was keyed by ``str()`` of a byte *list* on the send
side and ``str()`` of a byte *slice* on the receive side — a fragile match that
had to line up by hand. Here it is one internal key (``tuple(address)``) used on
both sides.

Interface:

- ``request(address, length)`` — send an RQ1 and block for the correlated
  reply, with the existing wait/retry-timeout semantics.
- ``set_and_await(address, data)`` — send a DT1 (set) and block for the reply
  the unit echoes at that address (the editor-mode handshake).
- ``send(message)`` — fire a pre-framed message with no reply expected.
- ``on_unsolicited(callback)`` — register the handler for device-initiated
  frames (program change, state changes the unit emits on its own).
- ``on_identity(callback)`` — register the handler notified when an identity
  reply is negotiated (device id is set on the link first).

``DeviceLink`` holds the ``Transport`` and installs its own inbound callback; it
decides whether an inbound frame is a correlated reply (hand back to the
waiter), an identity reply (negotiate the device id), or unsolicited (forward to
the registered callback). It uses the :class:`~pygt1000.sysex_codec.SysExCodec`
to frame requests and to recognise reply/identity frames, so the codec supplies
*how to frame*, the transport supplies *the wire*, and the link supplies *the
correlation*.
"""

import logging
import threading
from time import sleep

from .constants import DEVICE_ID_BCAST

logger = logging.getLogger(__name__)

# Wait/retry budget for a correlated reply: poll ``retry_count`` times, sleeping
# ``sleep_sec`` between polls. These own the timeout that used to live in
# ``GT1000.wait_recv_data``.
SLEEP_WAIT_SEC = 0.1
RETRY_COUNT = 100


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


class DeviceLink:
    """Owns the request/response correlation with the unit over a Transport."""

    def __init__(
        self,
        transport,
        codec,
        device_id=DEVICE_ID_BCAST,
        retry_count=RETRY_COUNT,
        sleep_sec=SLEEP_WAIT_SEC,
    ):
        self._transport = transport
        self._codec = codec
        self.device_id = device_id
        self._retry_count = retry_count
        self._sleep_sec = sleep_sec
        # offset-key -> reply data (None while the request is still in flight).
        self._pending = {}
        self._lock = threading.Semaphore(1)
        self._on_unsolicited = None
        self._on_identity = None
        self._transport.set_on_receive(self._on_receive)

    # -- registration ------------------------------------------------------

    def on_unsolicited(self, callback):
        """Register the handler for device-initiated ``(offset, data)`` frames."""
        self._on_unsolicited = callback

    def on_identity(self, callback):
        """Register the handler notified with the parsed ``IdentityReply`` once
        the device id has been negotiated onto the link."""
        self._on_identity = callback

    # -- outbound ----------------------------------------------------------

    def send(self, message):
        """Put a pre-framed message on the wire; no reply is expected."""
        logger.debug(f"sending: {bytes_as_hex(message)}")
        self._transport.send(message)

    def set(self, address_value):
        """Frame a DT1 that writes ``address_value`` (address bytes followed by
        the value byte(s)) and put it on the wire; no reply is expected. Mirrors
        how :meth:`request` frames RQ1 internally, so DT1 and RQ1 framing share
        one home and callers never touch the codec on the write path."""
        self.send(self._codec.encode_dt1(self.device_id, address_value))

    def request(self, address, length, override_checksum=None):
        """Send an RQ1 for ``address``/``length`` and block for the correlated
        reply. Returns the reply data, or ``None`` on timeout."""
        message = self._codec.encode_rq1(
            self.device_id, address, length, override_checksum
        )
        return self._exchange(message, address)

    def set_and_await(self, address, data):
        """Send a DT1 that writes ``data`` at ``address`` and block for the
        reply the unit echoes at that address. Returns the reply data, or
        ``None`` on timeout."""
        message = self._codec.encode_dt1(self.device_id, address + data)
        return self._exchange(message, address)

    # -- inbound -----------------------------------------------------------

    def _on_receive(self, message):
        # Installed as the transport's callback. Runs in the transport's inbound
        # context, so it swallows and logs exceptions — otherwise a failure here
        # is silent and very confusing to debug.
        try:
            if self.device_id == DEVICE_ID_BCAST and self._negotiate_identity(message):
                return
            parsed = self._codec.parse_data_reply(self.device_id, message)
            if parsed is None:
                return
            offset, data = parsed
            key = self._key(offset)
            with self._lock:
                if key in self._pending:
                    logger.debug("returning data")
                    self._pending[key] = data
                    return
            # Not a reply we are waiting for: the unit emitted it on its own.
            logger.debug("data emitted by the unit")
            if self._on_unsolicited is not None:
                self._on_unsolicited(offset, data)
        except Exception:
            logger.exception("DeviceLink._on_receive")

    def _negotiate_identity(self, message):
        reply = self._codec.parse_identity_reply(message)
        if reply is None:
            return False
        self.device_id = reply.device_id
        if self._on_identity is not None:
            self._on_identity(reply)
        return True

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _key(address):
        # One correlation key for both sides: a hashable snapshot of the address
        # bytes, so the send side (a list) and the receive side (a slice of the
        # inbound frame) compare equal.
        return tuple(address)

    def _exchange(self, message, address):
        key = self._key(address)
        with self._lock:
            self._pending[key] = None
        self.send(message)
        data = self._wait(key)
        with self._lock:
            self._pending.pop(key, None)
        return data

    def _wait(self, key):
        for _ in range(self._retry_count):
            with self._lock:
                if self._pending[key] is not None:
                    return self._pending[key]
            sleep(self._sleep_sec)
        return None
