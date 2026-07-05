"""Bring the device up in editor mode and keep it up.

``EditorSession`` owns the device-conversation *sequence* that brings the unit
online: request identity (with its own retry loop), send the editor-mode set,
then run the liveness check. ``DeviceLink`` owns request/response
*correlation* — but the *sequence* on top of it used to sit on ``GT1000`` as a
cluster of sibling methods (``open_ports`` / ``open_editor_mode`` /
``request_identity`` / ``_reopen_ports``), against the project's own rule that
protocol logic should not live on the facade. It also entangled three seams the
facade otherwise just wires together (``DeviceLink``, the ``Transport``
lifecycle, and ``KeepAlive``'s ``on_unresponsive``), and was testable only by
driving the whole facade.

The session holds the ``Transport`` (it owns open/close — the natural boundary,
since bringing the device up starts by opening the wire) and the ``DeviceLink``
(it sends the identity request, sets editor mode, and runs the liveness fetch).
It exposes:

- ``open()`` / ``close()`` — bring the transport up and run the open ritual, or
  tear the transport down.
- ``reopen()`` — the policy ``KeepAlive`` signals on an unresponsive device:
  close then open. Handing this to ``KeepAlive`` as ``on_unresponsive`` means the
  reopen path *is* the open path (``reopen`` simply calls ``open``), rather than
  two facade methods that had to be kept in sync.

The model-specific fx-block-count adjustment stays a facade concern: identity is
negotiated through ``DeviceLink``'s ``on_identity`` callback (which the facade
owns), so the facade applies its tweak from the negotiated ``IdentityReply``.
"""

import logging
from time import sleep

from .constants import (
    DEVICE_ID_BCAST,
    EDITOR_MODE_ADDESS_VALUE2,
    EDITOR_MODE_ADDRESS_FETCH3,
    EDITOR_MODE_ADDRESS_LEN3,
    EDITOR_MODE_ADDRESS_SET2,
    EDITOR_REPLY2,
    EDITOR_REPLY3,
    IDENTITY_REQUEST_MSG,
)
from .device_link import RETRY_COUNT, SLEEP_WAIT_SEC
from .transport import MIDI_PORT

logger = logging.getLogger(__name__)


class EditorSession:
    """Owns the open/reopen sequence that brings the device up in editor mode."""

    def __init__(
        self, transport, link, retry_count=RETRY_COUNT, sleep_sec=SLEEP_WAIT_SEC
    ):
        self._transport = transport
        self._link = link
        self._retry_count = retry_count
        self._sleep_sec = sleep_sec

    # -- lifecycle ---------------------------------------------------------

    def open(self, in_portname=MIDI_PORT, out_portname=MIDI_PORT):
        """Open the transport, then run the editor-mode handshake. Returns True
        only when the device came up (transport open + handshake acknowledged)."""
        if not self._transport.open(in_portname, out_portname):
            return False
        return self._open_editor_mode()

    def close(self):
        """Tear the transport down."""
        self._transport.close()

    def reopen(self, in_portname=MIDI_PORT, out_portname=MIDI_PORT):
        """The reopen policy for an unresponsive device: close the ports, then
        open them again. This shares the one ``open`` implementation instead of a
        second bespoke sequence kept in sync with it."""
        logger.warning("Device not responding, trying to reopen ports")
        self.close()
        if self.open(in_portname, out_portname) is True:
            logger.warning("Opening ports succeeded")
            return True
        logger.warning("Opening ports failed")
        return False

    # -- handshake ---------------------------------------------------------

    def request_identity(self):
        """Send the identity request and poll for the device id to be negotiated
        onto the link, retrying up to the configured budget. Returns True once a
        non-broadcast device id is in place."""
        # TODO: this should be a background thread so we update the ID if the
        # device comes online at some point
        for _ in range(self._retry_count):
            self._link.send(IDENTITY_REQUEST_MSG)
            sleep(self._sleep_sec)
            if self._link.device_id != DEVICE_ID_BCAST:
                logger.info(
                    f"Identity received: {self._link.device_id} "
                    f"({hex(self._link.device_id)})"
                )
                return True
        logger.warning(
            f"Identity not received, using broadcast {self._link.device_id} "
            f"({hex(self._link.device_id)})"
        )
        return False

    def _open_editor_mode(self):
        logger.info("Opening device in editor mode")
        # Device identification (the model substitution + fx-block-count tweak
        # happen in the facade's on_identity callback as the reply lands).
        if not self.request_identity():
            return False

        # The editor-mode set the unit echoes back, then a liveness fetch. The
        # two returned values may break if the device firmware changes them; the
        # fetch in particular looks like a simple "is the device responsive"
        # check rather than reading anything meaningful.
        data = self._link.set_and_await(
            EDITOR_MODE_ADDRESS_SET2, EDITOR_MODE_ADDESS_VALUE2
        )
        if data != EDITOR_REPLY2:
            return False
        logger.debug("command2 ok")

        data = self._link.request(EDITOR_MODE_ADDRESS_FETCH3, EDITOR_MODE_ADDRESS_LEN3)
        if data != EDITOR_REPLY3:
            return False
        logger.debug("command3 ok")
        logger.info("Device opened in editor mode")
        return True
