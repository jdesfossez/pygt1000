"""Characterise the SysEx wire format behind ``SysExCodec``.

The codec is pure: given the negotiated ``device_id`` it turns address-value
payloads into frames (DT1/RQ1) and reads frames back into payloads (data reply,
identity reply). No device, no transport, no threads — every case here is a byte
literal in, byte literal out. It complements ``AddressMap`` (which bytes) by
owning how those bytes are framed and how a frame is read back.
"""

from pygt1000.sysex_codec import SysExCodec


# --------------------------------------------------------------------------
# encode DT1 (set)
# --------------------------------------------------------------------------

def test_encode_dt1_full_bytes():
    codec = SysExCodec()
    # fx1 SW ON: payload [addr..., value], device id 0x7F (broadcast).
    message = codec.encode_dt1(0x7F, [0x10, 0x0, 0x23, 0x0, 0x1])
    assert message == [
        0xF0, 0x41, 0x7F, 0x0, 0x0, 0x0, 0x4F, 0x12,
        0x10, 0x0, 0x23, 0x0, 0x1, 0x4C, 0xF7,
    ]


def test_encode_dt1_substitutes_device_id():
    codec = SysExCodec()
    message = codec.encode_dt1(0x10, [0x10, 0x0, 0x23, 0x0, 0x1])
    assert message[2] == 0x10
    # The rest of the frame is unchanged; only the checksum follows the payload.
    assert message[:2] == [0xF0, 0x41]
    assert message[-1] == 0xF7


def test_encode_dt1_override_checksum():
    codec = SysExCodec()
    message = codec.encode_dt1(0x10, [0x0], override_checksum=[0x0])
    assert message[-2:] == [0x0, 0xF7]


# --------------------------------------------------------------------------
# encode RQ1 (request)
# --------------------------------------------------------------------------

def test_encode_rq1_full_bytes():
    codec = SysExCodec()
    # Read 1 byte from 0x7F,0x0,0x0,0x3 with device id 0x10 (constants comment).
    message = codec.encode_rq1(0x10, [0x7F, 0x0, 0x0, 0x3], [0x0, 0x0, 0x0, 0x1])
    assert message == [
        0xF0, 0x41, 0x10, 0x0, 0x0, 0x0, 0x4F, 0x11,
        0x7F, 0x0, 0x0, 0x3, 0x0, 0x0, 0x0, 0x1, 0x7D, 0xF7,
    ]


def test_encode_rq1_carries_command_id_and_wrapping():
    codec = SysExCodec()
    message = codec.encode_rq1(0x10, [0x10, 0x0, 0x23, 0x0], [0x0, 0x0, 0x0, 0x1])
    assert message[0] == 0xF0 and message[-1] == 0xF7
    assert 0x11 in message  # RQ1 command id


# --------------------------------------------------------------------------
# parse data reply (inbound offset + data)
# --------------------------------------------------------------------------

def _data_reply(device_id, offset, data):
    return [0xF0, 0x41, device_id, 0x0, 0x0, 0x0, 0x4F, 0x12] + offset + data + [0x0, 0xF7]


def test_parse_data_reply_splits_offset_and_data():
    codec = SysExCodec()
    message = _data_reply(0x10, [0x10, 0x0, 0x23, 0x0], [0x1])
    parsed = codec.parse_data_reply(0x10, message)
    assert parsed == ([0x10, 0x0, 0x23, 0x0], [0x1])


def test_parse_data_reply_rejects_other_device():
    codec = SysExCodec()
    message = _data_reply(0x05, [0x10, 0x0, 0x23, 0x0], [0x1])
    assert codec.parse_data_reply(0x10, message) is None


def test_parse_data_reply_rejects_non_dt1_frame():
    codec = SysExCodec()
    # An identity reply is not a DT1 data frame.
    message = [0xF0, 0x7E, 0x10, 0x6, 0x2, 0x41, 0x4F, 0x3]
    assert codec.parse_data_reply(0x10, message) is None


# --------------------------------------------------------------------------
# parse identity reply (model + device id)
# --------------------------------------------------------------------------

def _identity_message(rev1, rev2, device_id=0x10):
    return [
        0xF0, 0x7E, device_id, 0x6, 0x2, 0x41, 0x4F, 0x3,
        0x0, 0x0, rev1, 0x0, rev2, 0x0, 0xF7,
    ]


def test_parse_identity_reply_known_models():
    codec = SysExCodec()
    for rev1, rev2, model in [
        (0x00, 0x01, "GT-1000"),
        (0x01, 0x01, "GT-1000L"),
        (0x02, 0x00, "GT-1000CORE"),
    ]:
        parsed = codec.parse_identity_reply(_identity_message(rev1, rev2))
        assert parsed is not None
        assert parsed.device_id == 0x10
        assert parsed.model == model


def test_parse_identity_reply_unknown_model_still_carries_device_id():
    codec = SysExCodec()
    parsed = codec.parse_identity_reply(_identity_message(0x0F, 0x0F))
    assert parsed is not None
    assert parsed.device_id == 0x10
    assert parsed.model is None


def test_parse_identity_reply_rejects_wrong_length():
    codec = SysExCodec()
    assert codec.parse_identity_reply([0xF0, 0x7E, 0x10]) is None


def test_parse_identity_reply_rejects_wrong_manufacturer():
    codec = SysExCodec()
    message = _identity_message(0x00, 0x01)
    message[5] = 0x42  # not Roland
    assert codec.parse_identity_reply(message) is None
