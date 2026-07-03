"""Pin the Slider module directly: policy (which two params are a block's
sliders, including the eq and fx-name rules) plus resolution (range from the
AddressMap, current value from an injected reader). No device — the per-param
value read is a fake reader, so this exercises the module without any MIDI.
"""

import pytest

from pygt1000.address_map import AddressMap
from pygt1000.slider import Slider

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


def make_slider(address_map, fx_names=None, reader=None):
    """A Slider whose value reads come from ``reader`` (default: constant 0) and
    whose fx names come from the ``fx_names`` map."""
    if reader is None:
        reader = lambda fx_type, fx_id, option: 0  # noqa: E731
    names = fx_names if fx_names is not None else {}
    return Slider(address_map, lambda fx_id: names[fx_id], reader)


def _labels(pair):
    return tuple(None if s is None else s["label"] for s in pair)


# --------------------------------------------------------------------------
# Policy: non-fx blocks
# --------------------------------------------------------------------------

def test_non_fx_block_resolves_two_labelled_sliders(address_map):
    # comp is a single-instance block; the caller passes the stripped id "".
    s1, s2 = make_slider(address_map).sliders_for("comp", "", None)
    assert s1["label"] == "SUSTAIN"
    assert s2["label"] == "LEVEL"


def test_unknown_non_fx_block_has_no_sliders(address_map):
    assert make_slider(address_map).sliders_for("nope", "1", None) == (None, None)


# --------------------------------------------------------------------------
# Policy: eq is param-dependent
# --------------------------------------------------------------------------

def test_eq_parametric_uses_level1(address_map):
    pair = make_slider(address_map).sliders_for("eq", "1", "PARAMETRIC")
    assert _labels(pair) == ("LEVEL1", None)


def test_eq_non_parametric_uses_level(address_map):
    pair = make_slider(address_map).sliders_for("eq", "1", "GRAPHIC")
    assert _labels(pair) == ("LEVEL", None)


# --------------------------------------------------------------------------
# Policy: fx block keyed by the resolved effect name
# --------------------------------------------------------------------------

def test_fx_block_keyed_by_resolved_name(address_map):
    slider = make_slider(address_map, fx_names={"1": "DISTORTION"})
    pair = slider.sliders_for("fx", "1", None)
    assert _labels(pair) == ("DRIVE", "LEVEL")


def test_fx_name_with_no_sliders(address_map):
    slider = make_slider(address_map, fx_names={"1": "OCTAVE BASS"})
    assert slider.sliders_for("fx", "1", None) == (None, None)


# --------------------------------------------------------------------------
# Resolution: value from the reader, range from the AddressMap
# --------------------------------------------------------------------------

def test_value_comes_from_reader_and_range_from_map(address_map):
    reads = {}

    def reader(fx_type, fx_id, option):
        reads[(fx_type, fx_id, option)] = True
        return 7

    s1, _ = make_slider(address_map, reader=reader).sliders_for("comp", "", None)
    assert s1["value"] == 7
    assert s1["min"] is not None and s1["max"] is not None
    assert ("comp", "", "SUSTAIN") in reads


def test_none_param_is_not_read(address_map):
    # ac guitar sim has only slider1; slider2 must be None without a read.
    def reader(fx_type, fx_id, option):
        assert option is not None
        return 3

    slider = make_slider(address_map, fx_names={"1": "AC GUITAR SIM"}, reader=reader)
    s1, s2 = slider.sliders_for("fx", "1", None)
    assert s1["label"] == "LEVEL"
    assert s2 is None


# --------------------------------------------------------------------------
# Resolution: None handling differs between fx and non-fx
# --------------------------------------------------------------------------

def test_fx_slider_collapses_to_none_when_read_fails(address_map):
    slider = make_slider(
        address_map,
        fx_names={"1": "DISTORTION"},
        reader=lambda fx_type, fx_id, option: None,
    )
    assert slider.sliders_for("fx", "1", None) == (None, None)


def test_non_fx_slider_keeps_dict_when_read_returns_none(address_map):
    slider = make_slider(address_map, reader=lambda fx_type, fx_id, option: None)
    s1, s2 = slider.sliders_for("comp", "", None)
    assert s1 is not None and s1["value"] is None
    assert s1["label"] == "SUSTAIN"
