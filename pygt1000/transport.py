"""The MIDI transport seam.

Protocol logic (address arithmetic, message assembly, decode, the state model)
must not talk to ``rtmidi`` directly. A ``Transport`` is a narrow port with two
responsibilities:

- ``send(message)`` — put a raw message on the wire.
- an ``on_receive`` callback (installed via ``set_on_receive``) — deliver raw
  inbound messages back to the protocol.

Two adapters implement it. ``RtMidiTransport`` is production: it wraps rtmidi
in/out and port discovery. ``FakeTransport`` is for tests and replay: it records
what was sent and can inject canned device replies. Request/response
correlation (the offset-keyed wait/retry) lives on the protocol side of this
seam, driven by the inbound callback.

``rtmidi`` is a compiled C binding; it is imported lazily inside
``RtMidiTransport`` so importing this module (and the rest of the package, and
the test suite) never requires it.
"""

import logging
import time

logger = logging.getLogger(__name__)

# The portname usually contains an id that changes depending on the other
# devices present, so callers match on this prefix.
MIDI_PORT = "GT-1000:GT-1000 MIDI 1"


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


class Transport:
    """Narrow MIDI port: send raw messages, receive raw messages via callback."""

    def set_on_receive(self, callback):
        """Register the callback that raw inbound messages are handed to."""
        raise NotImplementedError

    def send(self, message):
        """Put a raw message on the wire."""
        raise NotImplementedError

    def open(self, in_portname=None, out_portname=None):
        """Bring the transport up. Returns True on success."""
        raise NotImplementedError

    def close(self):
        """Tear the transport down."""
        raise NotImplementedError


class FakeTransport(Transport):
    """Test double: records sent messages, injects canned device replies."""

    def __init__(self):
        self.sent = []
        self.opened = False
        self._on_receive = None

    def set_on_receive(self, callback):
        self._on_receive = callback

    def send(self, message):
        self.sent.append(message)

    def receive(self, message):
        """Inject a raw inbound message as if the device had sent it."""
        if self._on_receive is not None:
            self._on_receive(message)

    def open(self, in_portname=None, out_portname=None):
        self.opened = True
        return True

    def close(self):
        self.opened = False


class _MidiInputHandler:
    """rtmidi callback adapter: unpack the ``(message, deltatime)`` event and
    hand the raw message to the transport's ``on_receive`` callback."""

    def __init__(self, port, on_receive):
        self.port = port
        self._on_receive = on_receive
        self._wallclock = time.time()

    def __call__(self, event, data=None):
        message, deltatime = event
        self._wallclock += deltatime
        logger.debug(
            "[%s] @%0.6f %s" % (self.port, self._wallclock, bytes_as_hex(message))
        )
        if self._on_receive is not None:
            self._on_receive(message)


class RtMidiTransport(Transport):
    """Production adapter wrapping rtmidi in/out and port discovery."""

    def __init__(self, in_portname=MIDI_PORT, out_portname=MIDI_PORT):
        self.in_portname = in_portname
        self.out_portname = out_portname
        self.midi_in = None
        self.midi_out = None
        self._on_receive = None

    def set_on_receive(self, callback):
        self._on_receive = callback

    def _get_midi_exact_port_names(self, in_portname, out_portname):
        """The portname usually contains an id that can change depending on the
        other devices, so resolve the prefix to the exact current name."""
        import rtmidi

        tmp_midi_in = rtmidi.MidiIn()
        port_count = tmp_midi_in.get_port_count()
        exact_in_portname = None
        for i in range(port_count):
            if tmp_midi_in.get_port_name(i).startswith(in_portname):
                exact_in_portname = tmp_midi_in.get_port_name(i)
        if exact_in_portname is None:
            logger.error(
                f"Failed to find MIDI input port. Found {tmp_midi_in.get_ports()}"
            )

        tmp_midi_out = rtmidi.MidiOut()
        port_count = tmp_midi_out.get_port_count()
        exact_out_portname = None
        for i in range(port_count):
            if tmp_midi_out.get_port_name(i).startswith(out_portname):
                exact_out_portname = tmp_midi_out.get_port_name(i)
        if exact_out_portname is None:
            logger.error(
                f"Failed to find MIDI output port. Found {tmp_midi_out.get_ports()}"
            )

        return exact_in_portname, exact_out_portname

    def open(self, in_portname=None, out_portname=None):
        from rtmidi.midiutil import open_midiinput, open_midioutput

        if in_portname is not None:
            self.in_portname = in_portname
        if out_portname is not None:
            self.out_portname = out_portname
        logger.info(
            f"Opening MIDI ports: {self.in_portname} and {self.out_portname}"
        )
        in_portname, out_portname = self._get_midi_exact_port_names(
            self.in_portname, self.out_portname
        )
        if in_portname is None or out_portname is None:
            return False
        self.in_portname = in_portname
        self.out_portname = out_portname
        try:
            self.midi_out, _port_name = open_midioutput(out_portname)
        except (EOFError, KeyboardInterrupt):
            return False

        try:
            self.midi_in, _port_name = open_midiinput(in_portname)
        except (EOFError, KeyboardInterrupt):
            return False
        self.midi_in.ignore_types(sysex=False)
        self.midi_in.set_callback(_MidiInputHandler(in_portname, self._on_receive))
        return True

    def close(self):
        self.midi_out.close_port()
        self.midi_in.close_port()

    def send(self, message):
        self.midi_out.send_message(message)
