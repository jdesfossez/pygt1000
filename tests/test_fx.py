"""Characterise fx-block behaviour: normalization, section routing, the slider
mapping, the set/toggle commands, and the value helpers.

The slider-map test is the safety net for the Slider module extraction: it
pins, through ``gt._slider.sliders_for``, the exact (param1, param2) pair each
effect resolves to today. The module itself is pinned in test_slider.py.
"""

import pytest


# --------------------------------------------------------------------------
# Block normalization and section routing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fx_type,fx_id,expected",
    [
        ("preamp", 1, ("preamp", "A")),
        ("preamp", 2, ("preamp", "B")),
        ("comp", 1, ("comp", "")),   # single-instance block strips the id
        ("fx", 1, ("fx", 1)),        # multi-instance block keeps the id
        ("dist", 2, ("dist", 2)),
    ],
)
def test_normalize_fx_block(gt, fx_type, fx_id, expected):
    assert gt._address_map.normalize_block(fx_type, fx_id) == expected


def test_get_start_section_defaults_to_patch(gt):
    assert gt._address_map.start_section("fx", "1") == "patch (temporary patch)"


def test_get_start_section_fx4_is_patch3(gt):
    assert gt._address_map.start_section("fx", "4") == "patch3 (temporary patch)"


@pytest.mark.parametrize(
    "fx_id,fx_name,expected",
    [
        ("1", "CHORUS", "patch (temporary patch)"),
        ("1", "CHORUS BASS", "patch2 (temporary patch)"),
        ("2", "FLANGER BASS", "patch2 (temporary patch)"),
        ("1", "DISTORTION", "patch3 (temporary patch)"),
        ("1", "MASTERING FX", "patch3 (temporary patch)"),
        ("4", "CHORUS", "patch3 (temporary patch)"),
    ],
)
def test_get_fx_start_section(gt, fx_id, fx_name, expected):
    from pygt1000.constants import FX_TO_TABLE_SUFFIX

    suffix = FX_TO_TABLE_SUFFIX[fx_name]
    assert gt._address_map.fx_start_section(fx_id, suffix) == expected


# --------------------------------------------------------------------------
# Slider mapping (gt._slider.sliders_for) — full golden table
# --------------------------------------------------------------------------

# fx_type -> (slider1_label, slider2_label). None means "no slider".
NON_FX_SLIDERS = {
    "comp": ("SUSTAIN", "LEVEL"),
    "dist": ("DRIVE", "LEVEL"),
    "preamp": ("GAIN", "LEVEL"),
    "ns": ("THRESHOLD", "RELEASE"),
    "delay": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "mstDelay": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "chorus": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "reverb": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "pedalFx": ("EFFECT LEVEL", "DIRECT MIX"),
}

# fx block, keyed by the resolved effect name -> (slider1, slider2).
FX_NAME_SLIDERS = {
    "AC GUITAR SIM": ("LEVEL", None),
    "AC RESONANCE": ("LEVEL", None),
    "AUTO WAH": ("EFFECT LEVEL", "DIRECT MIX"),
    "CHORUS": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "CHORUS BASS": ("EFFECT LEVEL", "DEPTH"),
    "CLASSIC-VIBE": ("EFFECT LEVEL", "DEPTH"),
    "COMPRESSOR": ("LEVEL", "DIRECT MIX"),
    "DEFRETTER": ("EFFECT LEVEL", "DEPTH"),
    "DEFRETTER BASS": ("EFFECT LEVEL", "DIRECT MIX"),
    "DISTORTION": ("DRIVE", "LEVEL"),
    "FEEDBACKER": ("FEEDBACK", "OCT FEEDBACK"),
    "FLANGER": ("EFFECT LEVEL", "DIRECT MIX"),
    "FLANGER BASS": ("EFFECT LEVEL", "DIRECT MIX"),
    "HARMONIST": ("HR1:LEVEL", "DIRECT LEVEL"),
    "HUMANIZER": ("LEVEL", "DEPTH"),
    "MASTERING FX": ("TONE", "NATURAL"),
    "OCTAVE": ("OCTAVE LEVEL", "DIRECT LEVEL"),
    "OCTAVE BASS": (None, None),
    "OVERTONE": ("UPPER LEVEL", "DIRECT LEVEL"),
    "PAN": ("EFFECT LEVEL", "DIRECT MIX"),
    "PHASER": ("EFFECT LEVEL", "DIRECT MIX"),
    "PITCH SHIFTER": ("PS1:LEVEL", "DIRECT LEVEL"),
    "RING MOD": ("EFFECT LEVEL", "DIRECT MIX"),
    "ROTARY": ("EFFECT LEVEL", "DIRECT MIX"),
    "S-BEND": ("FALL TIME", "RISE TIME"),
    "SITAR SIM": ("EFFECT LEVEL", "DIRECT MIX"),
    "SLICER": ("EFFECT LEVEL", "DIRECT MIX"),
    "SLOW GEAR": ("LEVEL", "SENS"),
    "SLOW GEAR BASS": ("LEVEL", "SENS"),
    "SOUND HOLD": ("EFFECT LEVEL", "RISE TIME"),
    "TOUCH WAH": ("EFFECT LEVEL", "DIRECT MIX"),
    "TOUCH WAH BASS": (None, None),
    "TREMOLO": ("EFFECT LEVEL", "DIRECT MIX"),
    "VIBRATO": ("EFFECT LEVEL", "DIRECT MIX"),
}


@pytest.fixture
def echo_slider_labels(gt):
    """Decouple the mapping from MIDI I/O and range lookups: the injected value
    reader and the range lookup are stubbed, so ``sliders_for`` reports just the
    (label) policy each block resolves to. Range resolution is pinned separately
    in test_slider.py."""
    gt._slider._read_value = lambda fx_type, fx_id, option: 0
    gt._slider._value_range = lambda fx_type, fx_id, option: (0, 0)
    return gt


def _labels(pair):
    return tuple(None if s is None else s["label"] for s in pair)


@pytest.mark.parametrize("fx_type,expected", NON_FX_SLIDERS.items())
def test_non_fx_slider_map(echo_slider_labels, fx_type, expected):
    assert _labels(echo_slider_labels._slider.sliders_for(fx_type, "1", None)) == expected


def test_eq_slider_map_depends_on_param(echo_slider_labels):
    sliders_for = echo_slider_labels._slider.sliders_for
    assert _labels(sliders_for("eq", "1", "PARAMETRIC")) == ("LEVEL1", None)
    assert _labels(sliders_for("eq", "1", "GRAPHIC")) == ("LEVEL", None)


@pytest.mark.parametrize("fx_name,expected", FX_NAME_SLIDERS.items())
def test_fx_name_slider_map(echo_slider_labels, fx_name, expected):
    echo_slider_labels._state.set_fx_name("1", fx_name)
    assert _labels(echo_slider_labels._slider.sliders_for("fx", "1", None)) == expected


# --------------------------------------------------------------------------
# Commands (send_message mocked at the I/O boundary)
# --------------------------------------------------------------------------

@pytest.fixture
def captured(gt):
    """A GT1000 whose fake transport records outgoing messages instead of
    touching the wire; ``gt.sent`` is that record."""
    gt.sent = gt._transport.sent
    return gt


def test_toggle_fx_state_sends_and_updates(captured):
    from datetime import datetime

    gt = captured
    gt._state.record_scan("comp", [{"fx_id": "", "state": "OFF"}], datetime.now())
    gt.toggle_fx_state("comp", 1, "ON")
    assert gt.get_state()["comp"][0]["state"] == "ON"
    # The built message is the fx1-style SW=ON DT1 message for the comp block.
    assert gt.sent[-1][0] == 0xF0 and gt.sent[-1][-1] == 0xF7


def test_set_fx_value_non_fx_sends_message(captured):
    gt = captured
    gt.set_fx_value("comp", 1, "SUSTAIN", 42)
    assert len(gt.sent) == 1
    assert gt.sent[0][:8] == [0xF0, 0x41, 0x7F, 0x0, 0x0, 0x0, 0x4F, 0x12]


def test_set_fx_value_fx_uses_resolved_name(captured):
    gt = captured
    gt._state.set_fx_name(1, "CHORUS")
    gt.set_fx_value("fx", 1, "EFFECT LEVEL", 10)
    assert len(gt.sent) == 1


def test_set_fx_type_type_sends_message(captured):
    gt = captured
    gt.set_fx_type_type("fx", 1, "CHORUS")
    assert len(gt.sent) == 1


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------

def test_get_fx_value_from_value_name(gt):
    assert gt.get_fx_value_from_value_name("fx", "TYPE", "CHORUS") == 3


def test_get_fx_value_from_value_name_unknown(gt):
    assert gt.get_fx_value_from_value_name("fx", "TYPE", "NOPE") is None


def test_get_all_fx_types_excludes_bass_variants(gt):
    types = gt.get_all_fx_types("fx")
    assert "CHORUS" in types
    for excluded in ["DEFRETTER BASS", "OCTAVE BASS", "SLOW GEAR BASS", "TOUCH WAH BASS"]:
        assert excluded not in types


def test_get_all_fx_types_empty_for_ns_and_delay(gt):
    assert gt.get_all_fx_types("ns") == []
    assert gt.get_all_fx_types("delay") == []


@pytest.mark.parametrize(
    "fx_type,expected",
    [("fx", "PatchFx"), ("mstDelay", "PatchMstDelay"), ("pedalFx", "PatchPedalFx")],
)
def test_fx_type_table_name(gt, fx_type, expected):
    assert gt.fx_type_table_name(fx_type) == expected
