"""Characterise the extracted ``AddressMap`` encode-path interface.

Slice 1 of the architecture-deepening PRD moves the spec-table load and the
forward (encode) path behind one narrow module. These tests pin that interface
directly (independent of ``GT1000``): ``address_for`` / ``value_range`` and the
data-driven patch/patch2/patch3 section routing. Golden addresses are the same
known-good literals used by ``test_encoding.py``.
"""

import pytest

from pygt1000.address_map import AddressMap


# The fx_types list the map is built around (mirrors GT1000.fx_types).
FX_TYPES = [
    "comp", "dist", "preamp", "ns", "eq", "delay",
    "mstDelay", "chorus", "fx", "pedalFx", "reverb",
]


@pytest.fixture
def amap():
    return AddressMap(FX_TYPES)


# --------------------------------------------------------------------------
# Spec-table load: the six registries live on the module
# --------------------------------------------------------------------------

def test_owns_the_spec_registries(amap):
    assert amap.fx_types_count == {
        "comp": 1, "dist": 2, "preamp": 2, "ns": 2, "eq": 4, "delay": 4,
        "mstDelay": 1, "chorus": 1, "fx": 4, "pedalFx": 1, "reverb": 1,
    }
    assert amap.fx_tables["comp"] == "PatchComp"
    assert set(amap.offset_in_patch_tables) == {"Patch", "Patch2", "Patch3"}
    assert "PatchFx" in amap.last_byte_option
    assert "Patch" not in amap.last_byte_option
    assert amap.first_two_bytes


# --------------------------------------------------------------------------
# address_for: forward path
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
def test_address_for_base_offsets(amap, fx_type, fx_id, setting, expected):
    section = amap.start_section(fx_type, fx_id or "1")
    assert amap.address_for(section, f"{fx_type}{fx_id}", setting) == expected


def test_address_for_named_value_appends_byte(amap):
    section = amap.start_section("fx", "1")
    addr = amap.address_for(section, "fx1", "SW")
    assert amap.address_for(section, "fx1", "SW", "ON") == addr + [0x1]


def test_address_for_unknown_inputs_return_none(amap):
    section = amap.start_section("fx", "1")
    assert amap.address_for("no-such-section", "fx1", "SW") is None
    assert amap.address_for(section, "nope", "SW") is None
    assert amap.address_for(section, "fx1", "NOPE") is None


def test_value_range_is_a_pair(amap):
    section = amap.start_section("comp", "1")
    lo, hi = amap.value_range(section, "comp", "SUSTAIN")
    assert lo <= hi


# --------------------------------------------------------------------------
# Section routing expressed as data (not if-chains)
# --------------------------------------------------------------------------

def test_start_section_routes_fx4_to_patch3(amap):
    assert amap.start_section("fx", "4") == "patch3 (temporary patch)"
    assert amap.start_section("fx", "1") == "patch (temporary patch)"
    assert amap.start_section("comp", "") == "patch (temporary patch)"


@pytest.mark.parametrize(
    "fx_id,suffix,expected",
    [
        ("1", "ChorusBass", "patch2 (temporary patch)"),
        ("2", "FlangerBass", "patch2 (temporary patch)"),
        ("1", "Dist", "patch3 (temporary patch)"),
        ("3", "MasterFx", "patch3 (temporary patch)"),
        ("4", "Overtone", "patch3 (temporary patch)"),
        ("4", "ChorusBass", "patch3 (temporary patch)"),
        ("1", "Overtone", "patch (temporary patch)"),
    ],
)
def test_fx_start_section_routes_by_suffix(amap, fx_id, suffix, expected):
    assert amap.fx_start_section(fx_id, suffix) == expected
