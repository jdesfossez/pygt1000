"""Characterise the forward (encode) path: address arithmetic, message
assembly, checksums, and identity-reply parsing.

Everything here is pure — no wire, no threads.
"""

import pytest

from pygt1000.constants import (
    SYSEX_START,
    NON_RT_MSG,
    GEN_INFO,
    IDENTITY_REPLY,
    MANUFACTURER_ID,
    GT1000_FAMILY,
)


# --------------------------------------------------------------------------
# Address construction (_construct_address_value)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fx_type,fx_id,setting,expected",
    [
        ("comp", "", "SW", [0x10, 0x0, 0x12, 0x0]),
        ("comp", "", "SUSTAIN", [0x10, 0x0, 0x12, 0x2]),
        ("dist", "1", "SW", [0x10, 0x0, 0x13, 0x0]),
        ("preamp", "A", "GAIN", [0x10, 0x0, 0x15, 0x2]),
        ("ns", "1", "THRESHOLD", [0x10, 0x0, 0x17, 0x1]),
        ("delay", "1", "SW", [0x10, 0x0, 0x1D, 0x0]),
        ("fx", "1", "TYPE", [0x10, 0x0, 0x23, 0x1]),
    ],
)
def test_construct_address_base_offsets(gt, fx_type, fx_id, setting, expected):
    section = gt._get_start_section(fx_type, fx_id or "1")
    addr = gt._construct_address_value(section, f"{fx_type}{fx_id}", setting, None)
    assert addr == expected


def test_construct_address_fx4_routes_to_patch3(gt):
    section = gt._get_start_section("fx", "4")
    assert section == "patch3 (temporary patch)"
    assert gt._construct_address_value(section, "fx4", "TYPE", None) == [0x10, 0x2, 0x1, 0x1]


def test_construct_address_with_named_value_appends_byte(gt):
    section = gt._get_start_section("fx", "1")
    # param=None gives address only; a named value appends the encoded byte.
    addr = gt._construct_address_value(section, "fx1", "SW", None)
    with_value = gt._construct_address_value(section, "fx1", "SW", "ON")
    assert with_value == addr + [0x1]


def test_construct_address_unknown_inputs_return_none(gt):
    assert gt._construct_address_value("no-such-section", "fx1", "SW", None) is None
    section = gt._get_start_section("fx", "1")
    assert gt._construct_address_value(section, "nope", "SW", None) is None
    assert gt._construct_address_value(section, "fx1", "NOPE", None) is None


# --------------------------------------------------------------------------
# Checksum
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "data,expected",
    [
        ([0x10, 0x0, 0x23, 0x0, 0x1], [0x4C]),
        ([0x10, 0x2, 0x1, 0x1, 0x3], [0x69]),
        ([0x0], [0x80]),
    ],
)
def test_calculate_checksum(gt, data, expected):
    assert gt._codec.calculate_checksum(data) == expected


def test_checksum_makes_payload_sum_multiple_of_128(gt):
    payload = [0x52, 0x0, 0x0, 0x0, 0x7]
    checksum = gt._codec.calculate_checksum(payload)
    assert (sum(payload) + checksum[0]) % 128 == 0


# --------------------------------------------------------------------------
# Message assembly
# --------------------------------------------------------------------------

def test_build_dt_message_full_bytes(gt):
    # The encode path is address arithmetic (AddressMap) framed by the codec.
    section = gt._get_start_section("fx", "1")
    address_value = gt._construct_address_value(section, "fx1", "SW", "ON")
    message = gt._codec.encode_dt1(gt.device_id, address_value)
    assert message == [
        0xF0, 0x41, 0x7F, 0x0, 0x0, 0x0, 0x4F, 0x12,
        0x10, 0x0, 0x23, 0x0, 0x1, 0x4C, 0xF7,
    ]


def test_build_dt_message_is_wrapped_sysex(gt):
    section = gt._get_start_section("comp", "1")
    address_value = gt._construct_address_value(section, "comp", "SW", "ON")
    message = gt._codec.encode_dt1(gt.device_id, address_value)
    assert message[0] == 0xF0  # SYSEX start
    assert message[-1] == 0xF7  # SYSEX end
    assert message[1] == MANUFACTURER_ID[0]


def test_build_rq_message_carries_length(gt):
    message = gt._codec.encode_rq1(
        gt.device_id, [0x10, 0x0, 0x23, 0x0], [0x0, 0x0, 0x0, 0x1]
    )
    assert message[0] == 0xF0 and message[-1] == 0xF7
    # RQ1 command id sits in the header right after MODEL_ID.
    assert 0x11 in message


def test_header_carries_current_device_id(gt):
    # The broadcast id (0x7F) is replaced by the negotiated device id.
    gt.device_id = 0x10
    section = gt._get_start_section("fx", "1")
    address_value = gt._construct_address_value(section, "fx1", "SW", "ON")
    message = gt._codec.encode_dt1(gt.device_id, address_value)
    assert message[2] == 0x10


# --------------------------------------------------------------------------
# Identity-reply parsing (_msg_identity_reply)
# --------------------------------------------------------------------------

def _identity_message(rev1, rev2, device_id=0x10):
    return [
        SYSEX_START[0], NON_RT_MSG[0], device_id, GEN_INFO[0], IDENTITY_REPLY[0],
        MANUFACTURER_ID[0], GT1000_FAMILY[0], GT1000_FAMILY[1],
        0x0, 0x0, rev1, 0x0, rev2, 0x0, 0xF7,
    ]


@pytest.mark.parametrize(
    "rev1,rev2,model",
    [
        (0x00, 0x01, "GT-1000"),
        (0x01, 0x01, "GT-1000L"),
        (0x02, 0x00, "GT-1000CORE"),
    ],
)
def test_identity_reply_sets_model_and_device_id(gt, rev1, rev2, model):
    assert gt._msg_identity_reply(_identity_message(rev1, rev2)) is True
    assert gt.model == model
    assert gt.device_id == 0x10


def test_identity_reply_rejects_wrong_length(gt):
    assert gt._msg_identity_reply([0xF0, 0x7E, 0x10]) is False


def test_identity_reply_rejects_wrong_manufacturer(gt):
    message = _identity_message(0x00, 0x01)
    message[5] = 0x42  # not Roland
    assert gt._msg_identity_reply(message) is False
