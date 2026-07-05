"""Characterise the ``EditorSession`` — the device-conversation *sequence* that
brings the unit up in editor mode and keeps it up.

``DeviceLink`` owns request/response *correlation*; the *sequence* on top of it
(request identity with its retry loop, send the editor-mode set, run the
liveness check) used to live as a cluster of sibling methods on ``GT1000``.
``EditorSession`` owns it now, holding the ``Transport`` (open/close) and the
``DeviceLink`` (identity request, editor-mode set, liveness fetch).

Everything here drives the public interface against a ``FakeTransport`` wired to
answer each step of the handshake synchronously — no facade, no threads.
"""

from pygt1000.constants import (
    DT1_COMMAND_ID,
    EDITOR_MODE_ADDRESS_FETCH3,
    EDITOR_MODE_ADDRESS_SET2,
    EDITOR_REPLY2,
    EDITOR_REPLY3,
    GEN_INFO,
    GT1000_FAMILY,
    IDENTITY_REPLY,
    IDENTITY_REQUEST_MSG,
    MANUFACTURER_ID,
    MODEL_ID,
    NON_RT_MSG,
    SYSEX_START,
)
from pygt1000.device_link import DeviceLink
from pygt1000.editor_session import EditorSession
from pygt1000.sysex_codec import SysExCodec
from pygt1000.transport import FakeTransport


DEVICE_ID = 0x10


def _data_reply(device_id, offset, data):
    """A DT1 data reply the unit would send for a read/set at ``offset``."""
    header = SYSEX_START + MANUFACTURER_ID + [device_id] + MODEL_ID + DT1_COMMAND_ID
    return header + offset + data + [0x00, 0xF7]


def _identity_reply(rev1, rev2, device_id=DEVICE_ID):
    return [
        SYSEX_START[0], NON_RT_MSG[0], device_id, GEN_INFO[0], IDENTITY_REPLY[0],
        MANUFACTURER_ID[0], GT1000_FAMILY[0], GT1000_FAMILY[1],
        0x0, 0x0, rev1, 0x0, rev2, 0x0, 0xF7,
    ]


def _device(device_id=DEVICE_ID, identity=True, editor2=EDITOR_REPLY2, editor3=EDITOR_REPLY3):
    """An ``auto_reply`` that answers the three handshake steps: identity
    request, the editor-mode set (echoed at SET2), and the liveness fetch
    (answered at FETCH3). Any step can be suppressed to drive a failure path."""

    def reply(msg):
        if msg == IDENTITY_REQUEST_MSG:
            return _identity_reply(0x00, 0x01, device_id) if identity else None
        address = msg[8:12]
        if address == EDITOR_MODE_ADDRESS_SET2:
            return None if editor2 is None else _data_reply(device_id, address, editor2)
        if address == EDITOR_MODE_ADDRESS_FETCH3:
            return None if editor3 is None else _data_reply(device_id, address, editor3)
        return None

    return reply


def _session(auto_reply=None):
    """An EditorSession on a fake transport, with a fast retry budget so the
    failure paths don't sleep for the production timeout."""
    transport = FakeTransport(auto_reply=auto_reply)
    link = DeviceLink(transport, SysExCodec(), retry_count=3, sleep_sec=0.0)
    session = EditorSession(transport, link, retry_count=3, sleep_sec=0.0)
    return session, transport, link


# --------------------------------------------------------------------------
# open(): the full bring-up handshake
# --------------------------------------------------------------------------

def test_open_runs_the_full_handshake_and_returns_true():
    session, transport, link = _session(auto_reply=_device())

    assert session.open() is True
    # The transport is up and the device id was negotiated from the identity
    # reply the fake answered with.
    assert transport.opened is True
    assert link.device_id == DEVICE_ID
    # The identity request, the editor-mode set, and the liveness fetch all went
    # out (fire-and-forget identity, DT1 set, RQ1 fetch).
    assert IDENTITY_REQUEST_MSG in transport.sent


def test_open_returns_false_when_the_transport_fails_to_open():
    class DeadTransport(FakeTransport):
        def open(self, in_portname=None, out_portname=None):
            return False

    transport = DeadTransport()
    link = DeviceLink(transport, SysExCodec(), retry_count=3, sleep_sec=0.0)
    session = EditorSession(transport, link, retry_count=3, sleep_sec=0.0)

    assert session.open() is False


def test_open_returns_false_when_identity_never_arrives():
    session, _t, link = _session(auto_reply=_device(identity=False))
    assert session.open() is False
    # No identity reply: the device id is never negotiated off the broadcast.
    assert link.device_id != DEVICE_ID


def test_open_returns_false_when_editor_mode_set_is_not_acknowledged():
    session, _t, _l = _session(auto_reply=_device(editor2=None))
    assert session.open() is False


def test_open_returns_false_when_the_liveness_check_fails():
    session, _t, _l = _session(auto_reply=_device(editor3=None))
    assert session.open() is False


# --------------------------------------------------------------------------
# close(): tear the transport down
# --------------------------------------------------------------------------

def test_close_tears_down_the_transport():
    session, transport, _l = _session(auto_reply=_device())
    session.open()
    session.close()
    assert transport.opened is False


# --------------------------------------------------------------------------
# reopen(): the KeepAlive on_unresponsive policy — close then open, one impl
# --------------------------------------------------------------------------

def test_reopen_closes_then_opens_sharing_the_open_path():
    session, transport, _l = _session(auto_reply=_device())
    order = []
    session.close = lambda: order.append("close")
    real_open = session.open
    session.open = lambda *a, **k: order.append("open") or real_open(*a, **k)

    assert session.reopen() is True
    assert order == ["close", "open"]


# --------------------------------------------------------------------------
# request_identity(): the retry loop that negotiates the device id
# --------------------------------------------------------------------------

def test_request_identity_returns_true_once_the_device_id_is_negotiated():
    session, _t, link = _session(auto_reply=_device())
    assert session.request_identity() is True
    assert link.device_id == DEVICE_ID


def test_request_identity_times_out_to_false_when_no_reply():
    session, _t, link = _session(auto_reply=_device(identity=False))
    assert session.request_identity() is False
