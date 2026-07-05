"""Pin the ``PatchState`` interface: apply / set_fx / snapshot / is_ready plus
the write helpers the refresh path drives (record_scan / set_sliders).

These assert on the module's observable behaviour, not on the raw state dict,
which is now private to the module.
"""

from datetime import datetime

import pytest

from pygt1000.address_map import DecodedValue
from pygt1000.patch_state import PatchState


FX_TYPES = ["comp", "fx"]


def _fx(fx_id="", state="OFF", name="comp", slider1=None, slider2=None):
    return {
        "fx_id": fx_id,
        "state": state,
        "name": name,
        "slider1": slider1,
        "slider2": slider2,
    }


@pytest.fixture
def ready_state():
    state = PatchState(FX_TYPES)
    state.record_scan("comp", [_fx(name="comp")], datetime.now())
    state.record_scan("fx", [_fx(fx_id="1", name="CHORUS")], datetime.now())
    return state


# -- is_ready ---------------------------------------------------------------

def test_not_ready_until_every_fx_type_scanned():
    state = PatchState(FX_TYPES)
    assert not state.is_ready()
    state.record_scan("comp", [_fx()], datetime.now())
    assert not state.is_ready()
    state.record_scan("fx", [_fx(fx_id="1")], datetime.now())
    assert state.is_ready()


# -- snapshot ---------------------------------------------------------------

def test_snapshot_is_a_copy(ready_state):
    snap = ready_state.snapshot()
    snap["comp"][0]["state"] = "MUTATED"
    assert ready_state.snapshot()["comp"][0]["state"] == "OFF"


# -- apply ------------------------------------------------------------------

def _decoded(fx_type, fx_id, value_name, str_value="", int_value=0):
    return DecodedValue(
        section="patch",
        table="table",
        name=fx_type,
        patch_table="patch_table",
        value_name=value_name,
        str_value=str_value,
        int_value=int_value,
        fx_type=fx_type,
        fx_id=fx_id,
    )


def test_apply_refuses_until_ready():
    state = PatchState(FX_TYPES)
    result = state.apply(_decoded("comp", "", "SW", "ON"))
    assert result.matched is False


def test_apply_switch(ready_state):
    result = ready_state.apply(_decoded("comp", "", "SW", "ON"))
    assert result.matched and not result.type_changed
    assert ready_state.snapshot()["comp"][0]["state"] == "ON"


def test_apply_type_change_flags_type_changed(ready_state):
    result = ready_state.apply(_decoded("fx", "1", "TYPE", "PHASER"))
    assert result.matched and result.type_changed
    assert ready_state.snapshot()["fx"][0]["name"] == "PHASER"


def test_apply_unknown_fx_id_does_not_match(ready_state):
    result = ready_state.apply(_decoded("fx", "99", "SW", "ON"))
    assert result.matched is False


def test_apply_slider_value(ready_state):
    ready_state.record_scan(
        "comp",
        [_fx(slider1={"label": "LEVEL", "value": 0})],
        datetime.now(),
    )
    result = ready_state.apply(_decoded("comp", "", "LEVEL", int_value=42))
    assert result.matched
    assert ready_state.snapshot()["comp"][0]["slider1"]["value"] == 42


# -- set_fx -----------------------------------------------------------------

def test_set_fx_single_instance_by_index(ready_state):
    ready_state.set_fx("comp", 1, "state", "ON")
    assert ready_state.snapshot()["comp"][0]["state"] == "ON"


def test_set_fx_multi_instance_by_id(ready_state):
    ready_state.record_scan(
        "fx",
        [_fx(fx_id="1", state="OFF"), _fx(fx_id="2", state="OFF")],
        datetime.now(),
    )
    ready_state.set_fx("fx", "2", "state", "ON")
    snap = ready_state.snapshot()
    assert snap["fx"][0]["state"] == "OFF"
    assert snap["fx"][1]["state"] == "ON"


# -- set_sliders ------------------------------------------------------------

def test_set_sliders_updates_matching_block(ready_state):
    s1 = {"label": "LEVEL", "value": 7}
    ready_state.set_sliders("fx", "1", s1, None)
    snap = ready_state.snapshot()
    assert snap["fx"][0]["slider1"] == s1
    assert snap["fx"][0]["slider2"] is None


# -- resolved fx name (the single owner) ------------------------------------

def test_fx_name_round_trips():
    state = PatchState(FX_TYPES)
    state.set_fx_name(1, "CHORUS")
    assert state.fx_name(1) == "CHORUS"


def test_fx_name_key_is_normalized_int_vs_str():
    # A caller passing an int and one passing a str must resolve the same
    # effect — the fragile str/int key mismatch that could silently pick the
    # wrong slider layout.
    state = PatchState(FX_TYPES)
    state.set_fx_name(1, "CHORUS")
    assert state.fx_name("1") == "CHORUS"
    state.set_fx_name("2", "PHASER")
    assert state.fx_name(2) == "PHASER"


def test_apply_type_change_updates_resolved_fx_name(ready_state):
    # The device-echo TYPE-change path writes the resolved name through the same
    # owner, not a second hand-synced dict.
    ready_state.apply(_decoded("fx", "1", "TYPE", "PHASER"))
    assert ready_state.fx_name("1") == "PHASER"
