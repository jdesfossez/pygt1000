"""Characterise the transport seam: the narrow ``Transport`` port that carries
raw MIDI messages, and the two adapters that implement it.

The protocol (address arithmetic, message assembly, decode, state) talks to a
``Transport`` and never to ``rtmidi``. ``FakeTransport`` records what the
protocol sent and can inject canned device replies, so command tests assert on
``sent`` and pipeline tests drive ``receive``.
"""

from pygt1000 import GT1000
from pygt1000.transport import FakeTransport, RtMidiTransport, Transport


# --------------------------------------------------------------------------
# The port contract
# --------------------------------------------------------------------------

def test_fake_transport_is_a_transport():
    assert isinstance(FakeTransport(), Transport)
    assert isinstance(RtMidiTransport(), Transport)


def test_send_reaches_the_transport():
    t = FakeTransport()
    gt = GT1000(transport=t)
    # GT1000 now drives the wire through DeviceLink.send.
    gt._link.send([0xF0, 0x01, 0xF7])
    assert t.sent == [[0xF0, 0x01, 0xF7]]


def test_receive_injects_an_inbound_message_into_the_protocol():
    t = FakeTransport()
    seen = []
    # A freshly wired transport delivers inbound bytes to the registered
    # callback (DeviceLink installs its own; here we assert the port primitive).
    t.set_on_receive(seen.append)
    t.receive([0xF0, 0x7E, 0x10, 0xF7])
    assert seen == [[0xF0, 0x7E, 0x10, 0xF7]]


def test_default_transport_is_rtmidi():
    assert isinstance(GT1000()._transport, RtMidiTransport)


# --------------------------------------------------------------------------
# Prefactor: _build_message must not mutate the shared header constant
# --------------------------------------------------------------------------

def test_build_message_does_not_mutate_shared_header():
    from pygt1000.constants import DT1_SYSEX_HEADER

    before = list(DT1_SYSEX_HEADER)
    gt = GT1000(transport=FakeTransport())
    gt.device_id = 0x10
    section = gt._address_map.start_section("fx", "1")
    address_value = gt._address_map.address_for(section, "fx1", "SW", "ON")
    gt._codec.encode_dt1(gt.device_id, address_value)
    assert DT1_SYSEX_HEADER == before
    # And the negotiated device id still lands in the message.
    msg = gt._codec.encode_dt1(gt.device_id, address_value)
    assert msg[2] == 0x10


# --------------------------------------------------------------------------
# rtmidi is confined to the production adapter
# --------------------------------------------------------------------------

def test_protocol_module_imports_no_rtmidi():
    import pygt1000.gt1000 as protocol

    assert not hasattr(protocol, "rtmidi")
