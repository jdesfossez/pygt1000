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
# Spec-table load, observed through the narrow interface (registries private)
# --------------------------------------------------------------------------

def test_block_counts_per_fx_type(amap):
    # Block counts derived from the Patch/Patch2/Patch3 tables at load time,
    # read through the accessor rather than the raw registry.
    expected = {
        "comp": 1, "dist": 2, "preamp": 2, "ns": 2, "eq": 4, "delay": 4,
        "mstDelay": 1, "chorus": 1, "fx": 4, "pedalFx": 1, "reverb": 1,
    }
    for fx_type, count in expected.items():
        assert amap.fx_block_count(fx_type) == count


def test_every_fx_type_resolves_to_a_value_table(amap):
    # Each declared fx_type resolves to a loaded, non-empty value table.
    for fx_type in amap.fx_types:
        table = amap.fx_value_table(fx_type)
        assert isinstance(table, dict) and table


def test_set_fx_block_count_overrides_the_load(amap):
    # The GT-1000CORE special case: fx has 3 blocks, not 4.
    assert amap.fx_block_count("fx") == 4
    amap.set_fx_block_count("fx", 3)
    assert amap.fx_block_count("fx") == 3


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
# normalize_block: the block-id -> address-form rule
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fx_type,fx_id,expected",
    [
        ("preamp", 1, ("preamp", "A")),   # preamp 1/2 -> A/B
        ("preamp", 2, ("preamp", "B")),
        ("comp", 1, ("comp", "")),        # single-instance block strips the id
        ("fx", 1, ("fx", 1)),             # multi-instance block keeps the id
        ("dist", 2, ("dist", 2)),
        ("preamp", "A", ("preamp", "A")),  # already-normalized: idempotent
        ("comp", "", ("comp", "")),
    ],
)
def test_normalize_block(amap, fx_type, fx_id, expected):
    assert amap.normalize_block(fx_type, fx_id) == expected


# --------------------------------------------------------------------------
# address_for_block: normalize + section + option-string + fx-suffix in one
# --------------------------------------------------------------------------

def test_address_for_block_matches_hand_glued_non_fx(amap):
    # The interface reproduces the four-step hand-gluing for the non-fx path:
    # normalize -> start_section -> f"{fx_type}{fx_id}" -> address_for.
    fx_type, fx_id = amap.normalize_block("comp", 1)
    section = amap.start_section(fx_type, fx_id)
    manual = amap.address_for(section, f"{fx_type}{fx_id}", "SUSTAIN")
    assert amap.address_for_block("comp", 1, "SUSTAIN") == manual == [0x10, 0x0, 0x12, 0x2]


def test_address_for_block_normalizes_preamp(amap):
    # preamp 1 addresses the "A" block; the caller passes the raw id.
    section = amap.start_section("preamp", "A")
    manual = amap.address_for(section, "preampA", "GAIN")
    assert amap.address_for_block("preamp", 1, "GAIN") == manual == [0x10, 0x0, 0x15, 0x2]


def test_address_for_block_appends_value_byte(amap):
    addr = amap.address_for_block("fx", 1, "SW")
    assert amap.address_for_block("fx", 1, "SW", "ON") == addr + [0x1]


def test_address_for_block_fx_suffix_path(amap):
    # With fx_name given, the option gets the resolved fx-table suffix and the
    # section routes through fx_start_section.
    from pygt1000.constants import FX_TO_TABLE_SUFFIX

    suffix = FX_TO_TABLE_SUFFIX["CHORUS"]
    section = amap.fx_start_section("1", suffix)
    manual = amap.address_for(section, f"fx1{suffix}", "EFFECT LEVEL")
    assert manual is not None
    assert amap.address_for_block("fx", 1, "EFFECT LEVEL", fx_name="CHORUS") == manual


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


# --------------------------------------------------------------------------
# decode: reverse path (address + value -> semantic dict)
# --------------------------------------------------------------------------

def test_decode_switch_field(amap):
    ret = amap.decode([0x10, 0x0, 0x23, 0x0], 0x1)
    assert ret.name == "fx1"
    assert ret.value_name == "SW"
    assert ret.str_value == "ON"
    assert ret.int_value == 0x1
    assert ret.fx_type == "fx"
    assert ret.fx_id == "1"


def test_decode_resolves_fx_sub_effect(amap):
    ret = amap.decode([0x10, 0x0, 0x24, 0x0], 0x1)
    assert ret.name == "fx1AGSim"
    assert ret.value_name == "BODY"
    assert ret.fx_table_suffix == "AGSim"
    assert ret.patch_table == "PatchFxAGSim"
    assert ret.fx_name == "AC GUITAR SIM"


def test_decode_rejects_bad_address_length(amap):
    assert amap.decode([0x10, 0x0, 0x23], 0x1) is None


def test_decode_rejects_list_value(amap):
    assert amap.decode([0x10, 0x0, 0x23, 0x0], [0x1]) is None


def test_decode_unknown_address_returns_none(amap):
    assert amap.decode([0x77, 0x77, 0x77, 0x77], 0x1) is None


# --------------------------------------------------------------------------
# address_for <-> decode are inverses (encode/decode share the one map)
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
def test_address_for_and_decode_roundtrip(amap, fx_type, fx_id, option, setting):
    section = amap.start_section(fx_type, fx_id or "1")
    address = amap.address_for(section, option, setting)
    table = amap.fx_value_table(fx_type)
    for value_name, int_value in table[setting]["values"].items():
        decoded = amap.decode(address, int_value)
        assert decoded is not None
        assert decoded.value_name == setting
        assert decoded.str_value == value_name
        assert decoded.int_value == int_value


# --------------------------------------------------------------------------
# value_for / types_for / fx_type_table_name: value helpers
# --------------------------------------------------------------------------

def test_value_for_resolves_named_value(amap):
    assert amap.value_for("fx", "TYPE", "CHORUS") == 3


def test_value_for_unknown_returns_none(amap):
    assert amap.value_for("fx", "TYPE", "NOPE") is None


def test_types_for_excludes_bass_variants(amap):
    types = amap.types_for("fx")
    assert "CHORUS" in types
    for excluded in [
        "DEFRETTER BASS", "OCTAVE BASS", "SLOW GEAR BASS", "TOUCH WAH BASS",
    ]:
        assert excluded not in types


def test_types_for_empty_for_ns_and_delay(amap):
    assert amap.types_for("ns") == []
    assert amap.types_for("delay") == []


@pytest.mark.parametrize(
    "fx_type,expected",
    [("fx", "PatchFx"), ("mstDelay", "PatchMstDelay"), ("pedalFx", "PatchPedalFx")],
)
def test_fx_type_table_name(amap, fx_type, expected):
    assert amap.fx_type_table_name(fx_type) == expected


# --------------------------------------------------------------------------
# value-table / ChainElement accessors (replacing raw registry reads)
# --------------------------------------------------------------------------

def test_fx_value_table_returns_the_types_value_table(amap):
    table = amap.fx_value_table("comp")
    assert "SW" in table
    assert table["SW"]["values"]["ON"] == 1


def test_fx_name_value_table_keyed_by_table_suffix(amap):
    # The resolved fx sub-effect table, addressed by its PatchFx* suffix.
    table = amap.fx_name_value_table("AGSim")
    assert "BODY" in table


def test_chain_element_name_and_int_are_inverses(amap):
    assert amap.chain_element_name(0) == "COMPRESSOR"
    assert amap.chain_element_int("COMPRESSOR") == 0
    assert amap.chain_element_name(48) == "MAINOUTR"
    assert amap.chain_element_int("MAINOUTR") == 48
