"""Pin the whole-block snapshot assembly directly.

``BlockSnapshot`` owns "give me the full current state of this block": read SW,
read TYPE (or synthesize a name for the ns/delay blocks that have no TYPE
field), record the fx block's resolved name through its owner, ask Slider for the
two sliders, and pack the dict — plus the block iteration that scans an fx type.
Its collaborators are injected, so the assembly resolves against fakes with no
wire; the block-iteration paths use a real ``AddressMap`` for the block counts.
"""

import pytest

from pygt1000.address_map import AddressMap
from pygt1000.block_snapshot import BlockSnapshot
from pygt1000.patch_state import FxBlock

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


class FakeReader:
    """A stand-in BlockReader: returns a canned value per setting and records
    every ``read`` it was asked for."""

    def __init__(self, values):
        self.values = values
        self.reads = []

    def read(self, fx_type, fx_id, setting):
        self.reads.append((fx_type, fx_id, setting))
        return self.values.get(setting)


class FakeSlider:
    """A stand-in Slider: records the ``(fx_type, fx_id, name)`` it resolved and
    returns a fixed pair."""

    def __init__(self, result=("S1", "S2")):
        self.result = result
        self.calls = []

    def sliders_for(self, fx_type, fx_id, name):
        self.calls.append((fx_type, fx_id, name))
        return self.result


def make_snapshot(address_map, values, slider_result=("S1", "S2")):
    reader = FakeReader(values)
    slider = FakeSlider(slider_result)
    names = []
    snap = BlockSnapshot(
        address_map, reader, slider, lambda fx_id, name: names.append((fx_id, name))
    )
    return snap, reader, slider, names


# --------------------------------------------------------------------------
# snapshot — one block's full presented state
# --------------------------------------------------------------------------

def test_snapshot_assembles_state_name_and_sliders(address_map):
    snap, reader, slider, names = make_snapshot(
        address_map, {"SW": "ON", "TYPE": "BOSS COMP"}
    )
    result = snap.snapshot("comp", "1")
    assert result == FxBlock(
        fx_id="1",
        state="ON",
        name="BOSS COMP",
        slider1="S1",
        slider2="S2",
    )
    # It read SW for state and TYPE for the name, and resolved sliders against
    # the TYPE-derived name.
    assert ("comp", "1", "SW") in reader.reads
    assert ("comp", "1", "TYPE") in reader.reads
    assert slider.calls == [("comp", "1", "BOSS COMP")]
    # A non-fx block does not write the fx-name owner.
    assert names == []


def test_snapshot_synthesizes_name_for_ns_and_delay(address_map):
    for fx_type in ("ns", "delay"):
        snap, reader, slider, _ = make_snapshot(address_map, {"SW": "ON"})
        result = snap.snapshot(fx_type, "2")
        # ns/delay have no TYPE field: the name is synthesized, TYPE unread.
        assert result.name == f"{fx_type}2"
        assert (fx_type, "2", "TYPE") not in reader.reads
        assert slider.calls == [(fx_type, "2", f"{fx_type}2")]


def test_snapshot_records_fx_name_through_the_owner(address_map):
    snap, _, slider, names = make_snapshot(
        address_map, {"SW": "ON", "TYPE": "CHORUS"}
    )
    snap.snapshot("fx", "1")
    # The fx block's resolved effect name is written through the owner, and
    # slider resolution sees that name.
    assert names == [("1", "CHORUS")]
    assert slider.calls == [("fx", "1", "CHORUS")]


def test_snapshot_without_sliders_omits_slider_keys(address_map):
    snap, _, slider, _ = make_snapshot(
        address_map, {"SW": "OFF", "TYPE": "BOSS COMP"}
    )
    result = snap.snapshot("comp", "1", get_sliders=False)
    # get_sliders=False leaves the two slider fields at their default None.
    assert result == FxBlock(fx_id="1", state="OFF", name="BOSS COMP")
    # Sliders are not resolved when they were not asked for.
    assert slider.calls == []


# --------------------------------------------------------------------------
# snapshot_all / snapshot_one — block iteration over an fx type
# --------------------------------------------------------------------------

def test_snapshot_all_scans_every_block_of_the_type(address_map):
    snap, _, slider, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "BOSS COMP"})
    out = snap.snapshot_all("preamp")
    # One snapshot per block; preamp has two, addressed A/B.
    assert len(out) == address_map.fx_block_count("preamp")
    assert [c[1] for c in slider.calls] == ["A", "B"]


def test_snapshot_one_finds_a_block_by_id(address_map):
    # The fx block keeps numeric ids (they pass through normalize_block), so a
    # caller-supplied id matches directly.
    snap, _, _, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "CHORUS"})
    # A specific id resolves that block.
    assert snap.snapshot_one("fx", 2).fx_id == 2
    # A falsy id takes the first block.
    assert snap.snapshot_one("fx", None).fx_id == 1


def test_snapshot_one_returns_none_when_no_block_matches(address_map):
    snap, _, _, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "CHORUS"})
    assert snap.snapshot_one("fx", 9) is None
