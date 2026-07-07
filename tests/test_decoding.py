"""Characterise the reverse (decode) path: ``lookup`` plus the received-message
pipeline that folds decoded values into ``PatchState`` (read via ``get_state``).

The roundtrip tests are the safety net for collapsing encode+decode into one
Address Map module: encode an address, decode it back, expect the same setting.
"""

import pytest

from pygt1000.constants import (
    SYSEX_START,
    MANUFACTURER_ID,
    MODEL_ID,
    DT1_COMMAND_ID,
    PROGRAM_CHANGE_OFFSET,
)


# --------------------------------------------------------------------------
# lookup (address + value -> semantic dict)
# --------------------------------------------------------------------------

def test_lookup_switch_field(gt):
    ret = gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)
    assert ret.name == "fx1"
    assert ret.value_name == "SW"
    assert ret.str_value == "ON"
    assert ret.int_value == 0x1
    assert ret.fx_type == "fx"
    assert ret.fx_id == "1"


def test_lookup_resolves_fx_sub_effect(gt):
    ret = gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)
    assert ret.name == "fx1AGSim"
    assert ret.value_name == "BODY"
    assert ret.fx_table_suffix == "AGSim"
    assert ret.patch_table == "PatchFxAGSim"
    assert ret.fx_name == "AC GUITAR SIM"


def test_lookup_rejects_bad_address_length(gt):
    assert gt.lookup([0x10, 0x0, 0x23], 0x1) is None


def test_lookup_accepts_list_value(gt):
    # Reply payloads are lists; a 1-element list decodes like the bare int
    # (multi-byte params made list values first-class).
    decoded = gt.lookup([0x10, 0x0, 0x23, 0x0], [0x1])
    assert decoded is not None
    assert decoded.int_value == 0x1


def test_lookup_unknown_address_returns_none(gt):
    assert gt.lookup([0x77, 0x77, 0x77, 0x77], 0x1) is None


# --------------------------------------------------------------------------
# encode <-> decode roundtrip
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fx_type,fx_id,option,setting",
    [
        ("fx", "1", "fx1", "SW"),
        ("comp", "", "comp", "SW"),
        ("dist", "1", "dist1", "SW"),
        ("delay", "1", "delay1", "SW"),
    ],
)
def test_encode_decode_roundtrip_switch(gt, fx_type, fx_id, option, setting):
    section = gt._address_map.start_section(fx_type, fx_id or "1")
    address = gt._address_map.address_for(section, option, setting, None)
    table = gt._address_map.fx_value_table(fx_type)
    for value_name, int_value in table[setting]["values"].items():
        decoded = gt.lookup(address, int_value)
        assert decoded is not None
        assert decoded.value_name == setting
        assert decoded.str_value == value_name
        assert decoded.int_value == int_value


# --------------------------------------------------------------------------
# inbound-message pipeline -> _process_data_from_unit
#
# Bytes are fed through the fake transport (``receive``), exactly as rtmidi
# would deliver them to the on_receive callback, rather than calling the
# protocol method directly.
# --------------------------------------------------------------------------

def _unit_message(device_id, offset, data):
    header = SYSEX_START + MANUFACTURER_ID + [device_id] + MODEL_ID + DT1_COMMAND_ID
    return header + offset + data + [0x00, 0xF7]


def test_received_switch_updates_state(gt_with_state):
    gt = gt_with_state
    gt._transport.receive(_unit_message(0x10, [0x10, 0x0, 0x23, 0x0], [0x1]))
    assert gt.get_state()["fx"][0].state == "ON"


def test_received_type_change_updates_name_and_queues_slider_refresh(gt_with_state):
    gt = gt_with_state
    # fx1 TYPE -> CHORUS (value 3) lives at [0x10, 0x0, 0x23, 0x1].
    gt._transport.receive(_unit_message(0x10, [0x10, 0x0, 0x23, 0x1], [0x3]))
    assert gt.get_state()["fx"][0].name == "CHORUS"
    # The resolved-fx-name owner is updated through the same apply, not a
    # loose facade-side dict.
    assert gt._state.fx_name(1) == "CHORUS"
    # A type change schedules a slider re-read for that block.
    assert any(t["type"] == "sliders" for t in gt._refresh.pending())


def test_received_message_from_other_device_is_ignored(gt_with_state):
    gt = gt_with_state
    before = gt.get_state()["fx"][0].state
    # device id in the header does not match the negotiated id.
    gt._transport.receive(_unit_message(0x05, [0x10, 0x0, 0x23, 0x0], [0x1]))
    assert gt.get_state()["fx"][0].state == before


def test_program_change_queues_full_refresh(gt_with_state):
    gt = gt_with_state
    gt._transport.receive(_unit_message(0x10, list(PROGRAM_CHANGE_OFFSET), [0x5]))
    assert {"type": "full"} in gt._refresh.pending()
