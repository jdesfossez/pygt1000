"""Pin the whole block-read seam directly.

``BlockSnapshot`` owns "give me the full current state of this block": slider
*policy* (which two params a block exposes, the eq and fx-name rules), slider
*resolution* (range from the AddressMap, current value from an injected reader),
the whole-block *assembly* (read SW, read TYPE or synthesize a name for the
ns/delay blocks that have no TYPE field, record the fx block's resolved name
through its owner, pack an :class:`FxBlock`), and the block iteration that scans
an fx type.

Its reads go through an injected BlockReader-shaped fake — ``read`` for a setting
(SW/TYPE), ``read_value`` for a param's current value — so the whole read
resolves against fakes with no wire; the block-iteration and range paths use a
real ``AddressMap``.
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
    """A stand-in BlockReader: ``read`` returns a canned value per setting (and
    records every read); ``read_value`` returns a canned param value (default 0,
    recording every value read)."""

    def __init__(self, values=None, value_reader=None):
        self.values = values or {}
        self._value_reader = value_reader or (lambda ft, fid, opt: 0)
        self.reads = []
        self.value_reads = []

    def read(self, fx_type, fx_id, setting):
        self.reads.append((fx_type, fx_id, setting))
        return self.values.get(setting)

    def read_value(self, fx_type, fx_id, option):
        self.value_reads.append((fx_type, fx_id, option))
        return self._value_reader(fx_type, fx_id, option)


def make_snapshot(address_map, values=None, value_reader=None, fx_names=None):
    """A BlockSnapshot wired to fakes: its setting/value reads come from a
    ``FakeReader``, its live fx names from ``fx_names``, and the fx-name owner
    write is captured in the returned ``names_written`` list."""
    reader = FakeReader(values, value_reader)
    fx_names = fx_names or {}
    names_written = []
    snap = BlockSnapshot(
        address_map,
        reader,
        lambda fx_id: fx_names.get(fx_id),
        lambda fx_id, name: names_written.append((fx_id, name)),
    )
    return snap, reader, names_written


def _labels(pair):
    return tuple(None if s is None else s.label for s in pair)


# --------------------------------------------------------------------------
# Slider policy: non-fx blocks
# --------------------------------------------------------------------------

def test_non_fx_block_resolves_two_labelled_sliders(address_map):
    # comp is a single-instance block; the caller passes the stripped id "".
    snap, _, _ = make_snapshot(address_map)
    s1, s2 = snap.sliders_for("comp", "", None)
    assert s1.label == "SUSTAIN"
    assert s2.label == "LEVEL"


def test_unknown_non_fx_block_has_no_sliders(address_map):
    snap, _, _ = make_snapshot(address_map)
    assert snap.sliders_for("nope", "1", None) == (None, None)


# --------------------------------------------------------------------------
# Slider policy: eq is param-dependent
# --------------------------------------------------------------------------

def test_eq_parametric_uses_level1(address_map):
    snap, _, _ = make_snapshot(address_map)
    assert _labels(snap.sliders_for("eq", "1", "PARAMETRIC")) == ("LEVEL1", None)


def test_eq_non_parametric_uses_level(address_map):
    snap, _, _ = make_snapshot(address_map)
    assert _labels(snap.sliders_for("eq", "1", "GRAPHIC")) == ("LEVEL", None)


# --------------------------------------------------------------------------
# Slider policy: fx block keyed by the resolved effect name
# --------------------------------------------------------------------------

def test_fx_block_keyed_by_resolved_name(address_map):
    snap, _, _ = make_snapshot(address_map, fx_names={"1": "DISTORTION"})
    assert _labels(snap.sliders_for("fx", "1", None)) == ("DRIVE", "LEVEL")


def test_fx_name_with_no_sliders(address_map):
    snap, _, _ = make_snapshot(address_map, fx_names={"1": "OCTAVE BASS"})
    assert snap.sliders_for("fx", "1", None) == (None, None)


# --------------------------------------------------------------------------
# Slider resolution: value from the reader, range from the AddressMap
# --------------------------------------------------------------------------

def test_value_comes_from_reader_and_range_from_map(address_map):
    snap, reader, _ = make_snapshot(
        address_map, value_reader=lambda ft, fid, opt: 7
    )
    s1, _ = snap.sliders_for("comp", "", None)
    assert s1.value == 7
    assert s1.min is not None and s1.max is not None
    assert ("comp", "", "SUSTAIN") in reader.value_reads


def test_none_param_is_not_read(address_map):
    # ac guitar sim has only slider1; slider2 must be None without a read.
    def reader(fx_type, fx_id, option):
        assert option is not None
        return 3

    snap, _, _ = make_snapshot(
        address_map, value_reader=reader, fx_names={"1": "AC GUITAR SIM"}
    )
    s1, s2 = snap.sliders_for("fx", "1", None)
    assert s1.label == "LEVEL"
    assert s2 is None


# --------------------------------------------------------------------------
# Slider resolution: None handling differs between fx and non-fx
# --------------------------------------------------------------------------

def test_fx_slider_collapses_to_none_when_read_fails(address_map):
    snap, _, _ = make_snapshot(
        address_map,
        value_reader=lambda ft, fid, opt: None,
        fx_names={"1": "DISTORTION"},
    )
    assert snap.sliders_for("fx", "1", None) == (None, None)


def test_non_fx_slider_keeps_value_when_read_returns_none(address_map):
    snap, _, _ = make_snapshot(address_map, value_reader=lambda ft, fid, opt: None)
    s1, s2 = snap.sliders_for("comp", "", None)
    assert s1 is not None and s1.value is None
    assert s1.label == "SUSTAIN"


# --------------------------------------------------------------------------
# snapshot — one block's full presented state
# --------------------------------------------------------------------------

def test_snapshot_assembles_state_name_and_sliders(address_map):
    snap, reader, names = make_snapshot(
        address_map, {"SW": "ON", "TYPE": "BOSS COMP"}
    )
    result = snap.snapshot("comp", "")
    assert result.state == "ON"
    assert result.name == "BOSS COMP"
    # The two sliders are resolved from the block's policy (comp -> SUSTAIN/LEVEL).
    assert _labels((result.slider1, result.slider2)) == ("SUSTAIN", "LEVEL")
    # It read SW for state and TYPE for the name.
    assert ("comp", "", "SW") in reader.reads
    assert ("comp", "", "TYPE") in reader.reads
    # A non-fx block does not write the fx-name owner.
    assert names == []


def test_snapshot_synthesizes_name_for_ns_and_delay(address_map):
    for fx_type in ("ns", "delay"):
        snap, reader, _ = make_snapshot(address_map, {"SW": "ON"})
        result = snap.snapshot(fx_type, "2")
        # ns/delay have no TYPE field: the name is synthesized, TYPE unread.
        assert result.name == f"{fx_type}2"
        assert (fx_type, "2", "TYPE") not in reader.reads


def test_snapshot_records_fx_name_through_the_owner(address_map):
    snap, _, names = make_snapshot(
        address_map, {"SW": "ON", "TYPE": "CHORUS"}, fx_names={"1": "CHORUS"}
    )
    result = snap.snapshot("fx", "1")
    # The fx block's resolved effect name is written through the owner, and
    # slider resolution sees that name (CHORUS -> EFFECT LEVEL / DIRECT LEVEL).
    assert names == [("1", "CHORUS")]
    assert _labels((result.slider1, result.slider2)) == (
        "EFFECT LEVEL",
        "DIRECT LEVEL",
    )


def test_snapshot_without_sliders_omits_slider_keys(address_map):
    snap, reader, _ = make_snapshot(
        address_map, {"SW": "OFF", "TYPE": "BOSS COMP"}
    )
    result = snap.snapshot("comp", "", get_sliders=False)
    # get_sliders=False leaves the two slider fields at their default None.
    assert result == FxBlock(fx_id="", state="OFF", name="BOSS COMP")
    # Sliders are not resolved when they were not asked for.
    assert reader.value_reads == []


# --------------------------------------------------------------------------
# snapshot_all / snapshot_one — block iteration over an fx type
# --------------------------------------------------------------------------

def test_snapshot_all_scans_every_block_of_the_type(address_map):
    snap, _, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "BOSS COMP"})
    out = snap.snapshot_all("preamp")
    # One snapshot per block; preamp has two, addressed A/B.
    assert len(out) == address_map.fx_block_count("preamp")
    assert [b.fx_id for b in out] == ["A", "B"]


def test_snapshot_one_finds_a_block_by_id(address_map):
    # The fx block keeps numeric ids (they pass through normalize_block), so a
    # caller-supplied id matches directly.
    snap, _, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "CHORUS"})
    # A specific id resolves that block.
    assert snap.snapshot_one("fx", 2).fx_id == 2
    # A falsy id takes the first block.
    assert snap.snapshot_one("fx", None).fx_id == 1


def test_snapshot_one_returns_none_when_no_block_matches(address_map):
    snap, _, _ = make_snapshot(address_map, {"SW": "ON", "TYPE": "CHORUS"})
    assert snap.snapshot_one("fx", 9) is None
