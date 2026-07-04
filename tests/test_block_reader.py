"""Pin the block-read pipeline directly.

``BlockReader`` owns address -> fetch -> decode for a block, with the device read
(``fetch``) injected so it resolves against a fake reader with no wire. The
byte->label decode is shared with the inbound ``AddressMap.decode`` path via
``AddressMap.read_label``; this file also pins that helper against known-good
spec values.
"""

import pytest

from pygt1000.address_map import AddressMap
from pygt1000.block_reader import BlockReader

FX_TYPES = [
    "comp",
    "dist",
    "preamp",
    "ns",
    "eq",
    "delay",
    "mstDelay",
    "chorus",
    "fx",
    "pedalFx",
    "reverb",
]


@pytest.fixture
def address_map():
    return AddressMap(FX_TYPES)


# --------------------------------------------------------------------------
# AddressMap.read_label — the shared byte->label reverse map
# --------------------------------------------------------------------------

def test_read_label_maps_raw_byte_to_its_name(address_map):
    table = address_map.fx_value_table("comp")
    # comp SW: {'OFF': 0, 'ON': 1}; comp TYPE: {'BOSS COMP': 0, 'X-COMP': 1, ...}
    assert address_map.read_label(table, "SW", 1) == "ON"
    assert address_map.read_label(table, "TYPE", 2) == "D-COMP"


def test_read_label_falls_back_to_raw_when_unmapped(address_map):
    table = address_map.fx_value_table("comp")
    # 99 is not a named SW value; the raw byte comes back unchanged.
    assert address_map.read_label(table, "SW", 99) == 99


# --------------------------------------------------------------------------
# BlockReader — address -> fetch -> decode, with fetch injected
# --------------------------------------------------------------------------

def make_reader(address_map, byte, fx_names=None):
    """A BlockReader whose injected ``fetch`` records the offset it was asked
    for and returns ``[byte]`` (or ``None`` to model no reply)."""
    calls = {}

    def fetch(offset, length):
        calls["offset"] = offset
        calls["length"] = length
        return None if byte is None else [byte]

    names = fx_names if fx_names is not None else {}
    reader = BlockReader(address_map, fetch, lambda fx_id: names[fx_id])
    return reader, calls


def test_non_fx_read_decodes_the_byte_to_its_label(address_map):
    reader, calls = make_reader(address_map, 1)
    # comp SW: {'OFF': 0, 'ON': 1}
    assert reader.read("comp", "1", "SW") == "ON"
    # It addressed the block through the map, not by re-deriving bytes.
    assert calls["offset"] == address_map.address_for_block("comp", "1", "SW")
    assert calls["length"] == [0x0, 0x0, 0x0, 0x1]


def test_non_fx_read_just_range_returns_the_raw_byte(address_map):
    reader, _ = make_reader(address_map, 42)
    assert reader.read("comp", "1", "SUSTAIN", just_range=True) == 42


def test_fx_read_decodes_through_the_resolved_sub_effect_table(address_map):
    reader, calls = make_reader(address_map, 5, fx_names={"1": "CHORUS"})
    # CHORUS TYPE: {..., 'CE-1 CHORUS': 5, ...}
    assert reader.read_fx("fx", "1", "TYPE") == "CE-1 CHORUS"
    assert calls["offset"] == address_map.address_for_block(
        "fx", "1", "TYPE", fx_name="CHORUS"
    )


def test_missing_read_is_none_on_both_paths(address_map):
    reader, _ = make_reader(address_map, None, fx_names={"1": "CHORUS"})
    assert reader.read("comp", "1", "SW") is None
    assert reader.read_fx("fx", "1", "TYPE") is None


def test_read_value_dispatches_fx_vs_non_fx(address_map):
    # non-fx: the raw byte (just_range), no label decode.
    reader, _ = make_reader(address_map, 1)
    assert reader.read_value("comp", "1", "SUSTAIN") == 1
    # fx: routed through the sub-effect table (label when mapped).
    reader, _ = make_reader(address_map, 5, fx_names={"1": "CHORUS"})
    assert reader.read_value("fx", "1", "TYPE") == "CE-1 CHORUS"
