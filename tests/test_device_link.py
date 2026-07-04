"""Characterise the ``DeviceLink`` — the request/response conversation that
sits on top of the ``Transport`` seam.

``DeviceLink`` owns the offset-keyed correlation, the semaphore, and the
wait/retry loop that used to be smeared across ``GT1000.send_message`` /
``fetch_mem`` / ``wait_recv_data`` / ``process_received_message``. It decides
whether an inbound frame is a correlated reply (hand back to the ``request``
waiter) or unsolicited (forward to the registered callback), and negotiates the
device id from identity replies.

Everything here drives the public interface against a ``FakeTransport``. No
threads: a request completes synchronously because the fake is wired to answer
the send with a canned device reply.
"""

from pygt1000.constants import (
    DEVICE_ID_BCAST,
    DT1_COMMAND_ID,
    GEN_INFO,
    GT1000_FAMILY,
    IDENTITY_REPLY,
    MANUFACTURER_ID,
    MODEL_ID,
    NON_RT_MSG,
    RQ1_COMMAND_ID,
    SYSEX_START,
)
from pygt1000.device_link import DeviceLink
from pygt1000.sysex_codec import SysExCodec
from pygt1000.transport import FakeTransport


DEVICE_ID = 0x10


def _link(transport, **kwargs):
    """A DeviceLink on the given transport with a fast, negotiated default so
    timeout tests don't sleep for the production retry budget."""
    kwargs.setdefault("device_id", DEVICE_ID)
    kwargs.setdefault("retry_count", 3)
    kwargs.setdefault("sleep_sec", 0.0)
    return DeviceLink(transport, SysExCodec(), **kwargs)


def _data_reply(device_id, offset, data):
    """A DT1 data reply the unit would send for a read at ``offset``."""
    header = SYSEX_START + MANUFACTURER_ID + [device_id] + MODEL_ID + DT1_COMMAND_ID
    return header + offset + data + [0x00, 0xF7]


def _identity_reply(rev1, rev2, device_id=DEVICE_ID):
    return [
        SYSEX_START[0], NON_RT_MSG[0], device_id, GEN_INFO[0], IDENTITY_REPLY[0],
        MANUFACTURER_ID[0], GT1000_FAMILY[0], GT1000_FAMILY[1],
        0x0, 0x0, rev1, 0x0, rev2, 0x0, 0xF7,
    ]


# --------------------------------------------------------------------------
# request(address, length): RQ1 out, correlated reply back
# --------------------------------------------------------------------------

def test_request_frames_an_rq1_and_returns_the_correlated_reply():
    address = [0x10, 0x0, 0x23, 0x0]
    t = FakeTransport(auto_reply=lambda msg: _data_reply(DEVICE_ID, address, [0x1]))
    link = _link(t)

    data = link.request(address, [0x0, 0x0, 0x0, 0x1])

    assert data == [0x1]
    # The outgoing frame is an RQ1 carrying our negotiated device id.
    sent = t.sent[0]
    assert sent[0] == SYSEX_START[0] and sent[-1] == 0xF7
    assert sent[2] == DEVICE_ID
    assert RQ1_COMMAND_ID[0] in sent


def test_request_times_out_to_none_when_no_reply():
    t = FakeTransport()  # nothing answers the send
    link = _link(t)
    assert link.request([0x10, 0x0, 0x23, 0x0], [0x0, 0x0, 0x0, 0x1]) is None


def test_request_ignores_a_reply_for_a_different_address():
    address = [0x10, 0x0, 0x23, 0x0]
    other = [0x10, 0x0, 0x24, 0x0]
    t = FakeTransport(auto_reply=lambda msg: _data_reply(DEVICE_ID, other, [0x9]))
    link = _link(t)
    # The reply is for another offset, so the waiter never sees it.
    assert link.request(address, [0x0, 0x0, 0x0, 0x1]) is None


# --------------------------------------------------------------------------
# The correlation key lines up on both sides (no str(list) vs str(slice))
# --------------------------------------------------------------------------

def test_a_correlated_reply_is_not_forwarded_as_unsolicited():
    address = [0x10, 0x0, 0x23, 0x0]
    t = FakeTransport(auto_reply=lambda msg: _data_reply(DEVICE_ID, address, [0x1]))
    link = _link(t)
    unsolicited = []
    link.on_unsolicited(lambda offset, data: unsolicited.append((offset, data)))

    assert link.request(address, [0x0, 0x0, 0x0, 0x1]) == [0x1]
    # A reply the request is waiting for must not leak to the unsolicited path.
    assert unsolicited == []


# --------------------------------------------------------------------------
# send(message): fire and forget
# --------------------------------------------------------------------------

def test_send_puts_the_raw_message_on_the_wire_without_waiting():
    t = FakeTransport()
    link = _link(t)
    link.send([0xF0, 0x01, 0xF7])
    assert t.sent == [[0xF0, 0x01, 0xF7]]


# --------------------------------------------------------------------------
# set(address_value): DT1 framed internally, fire and forget
# --------------------------------------------------------------------------

def test_set_frames_a_dt1_and_puts_it_on_the_wire():
    address_value = [0x10, 0x0, 0x23, 0x0, 0x1]
    t = FakeTransport()
    link = _link(t)

    link.set(address_value)

    # The bytes on the wire are exactly what the codec's DT1 framing produces
    # for the negotiated device id — the write path no longer frames by hand.
    assert t.sent == [SysExCodec().encode_dt1(DEVICE_ID, address_value)]


def test_set_does_not_block_for_a_reply():
    # No auto_reply is wired; set must return without waiting on the retry loop.
    t = FakeTransport()
    link = _link(t)
    assert link.set([0x10, 0x0, 0x23, 0x0, 0x1]) is None


# --------------------------------------------------------------------------
# on_unsolicited: device-initiated frames
# --------------------------------------------------------------------------

def test_unsolicited_frame_reaches_the_registered_callback():
    t = FakeTransport()
    link = _link(t)
    seen = []
    link.on_unsolicited(lambda offset, data: seen.append((offset, data)))
    # No request is pending, so this inbound data reply is device-initiated.
    t.receive(_data_reply(DEVICE_ID, [0x10, 0x0, 0x23, 0x0], [0x1]))
    assert seen == [([0x10, 0x0, 0x23, 0x0], [0x1])]


def test_frame_for_a_different_device_is_ignored():
    t = FakeTransport()
    link = _link(t)
    seen = []
    link.on_unsolicited(lambda offset, data: seen.append((offset, data)))
    t.receive(_data_reply(0x05, [0x10, 0x0, 0x23, 0x0], [0x1]))
    assert seen == []


# --------------------------------------------------------------------------
# Identity negotiation
# --------------------------------------------------------------------------

def test_identity_reply_sets_device_id_and_notifies():
    t = FakeTransport()
    link = _link(t, device_id=DEVICE_ID_BCAST)
    replies = []
    link.on_identity(replies.append)

    t.receive(_identity_reply(0x00, 0x01))

    assert link.device_id == DEVICE_ID
    assert len(replies) == 1
    assert replies[0].model == "GT-1000"


def test_identity_reply_ignored_once_device_id_is_negotiated():
    t = FakeTransport()
    link = _link(t, device_id=DEVICE_ID)  # already negotiated
    replies = []
    link.on_identity(replies.append)
    t.receive(_identity_reply(0x00, 0x01, device_id=0x20))
    # Already negotiated: a stray identity reply must not re-open negotiation.
    assert link.device_id == DEVICE_ID
    assert replies == []
