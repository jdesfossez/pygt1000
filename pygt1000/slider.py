"""Slider — policy meets mechanism.

Gathers the slider knowledge that used to be split three ways inside
``GT1000``: the *policy* (which two params are a block's sliders), the *eq*
special case, and the *resolution* (a param's value range from the AddressMap
plus its current value read over MIDI). The device dependency — reading a
param's current value — is injected as a value-reader, so the module resolves
sliders against a fake reader in tests without a device.
"""

from dataclasses import dataclass
from typing import Optional

from .constants import FX_TO_TABLE_SUFFIX


@dataclass
class SliderValue:
    """One resolved slider: its current ``value`` (``None`` if the read
    failed / was skipped), the ``label`` naming the param it controls, and the
    param's ``min``/``max`` range from the AddressMap. Produced here by
    :meth:`Slider._resolve`; carried on an fx block's ``slider1``/``slider2`` and
    mutated in place by :meth:`pygt1000.patch_state.PatchState.apply` when the
    device echoes a new value. Mutable for that in-place update."""

    value: Optional[int]
    label: str
    min: int
    max: int


class Slider:
    # Which two params are a block's sliders (the policy). Either entry may be
    # None, meaning "no slider"; a block/effect absent from these maps also has
    # no sliders.
    #
    # Non-fx blocks, keyed by fx_type. ``eq`` is the one param-dependent case
    # and is resolved in ``sliders_for`` rather than living as data here.
    NON_FX_SLIDER_PARAMS = {
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

    # fx block, keyed by the resolved effect name. OCTAVE BASS and TOUCH WAH
    # BASS map to no sliders; any name not listed does too.
    FX_NAME_SLIDER_PARAMS = {
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

    def __init__(self, address_map, fx_name_for, read_value):
        """``address_map`` supplies value ranges; ``fx_name_for(fx_id)`` returns
        the currently resolved effect name for an fx block (read live, so it
        follows the device);``read_value(fx_type, fx_id, option)`` reads a
        param's current value over MIDI (returns the value, or None)."""
        self._address_map = address_map
        self._fx_name_for = fx_name_for
        self._read_value = read_value

    def sliders_for(self, fx_type, fx_id, param_name):
        """Resolve a block's two sliders as ``(slider1, slider2)``; each is a
        :class:`SliderValue` or None."""
        # eq is the one param-dependent block; kept out of the data tables.
        if fx_type == "eq":
            param1 = "LEVEL1" if param_name == "PARAMETRIC" else "LEVEL"
            return self._resolve(fx_type, fx_id, param1), None

        if fx_type == "fx":
            param1, param2 = self.FX_NAME_SLIDER_PARAMS.get(
                self._fx_name_for(fx_id), (None, None)
            )
        else:
            param1, param2 = self.NON_FX_SLIDER_PARAMS.get(fx_type, (None, None))

        return (
            self._resolve(fx_type, fx_id, param1),
            self._resolve(fx_type, fx_id, param2),
        )

    def _resolve(self, fx_type, fx_id, option):
        """One slider as a :class:`SliderValue`: its range (from the map) +
        current value (from the reader), or None when the param is None. On the
        fx path a missing read collapses the whole slider to None; elsewhere the
        SliderValue is kept."""
        if option is None:
            return None
        value_range = self._value_range(fx_type, fx_id, option)
        value = self._read_value(fx_type, fx_id, option)
        if fx_type == "fx" and value is None:
            return None
        return SliderValue(
            value=value,
            label=option,
            min=value_range[0],
            max=value_range[1],
        )

    def _value_range(self, fx_type, fx_id, option):
        if fx_type == "fx":
            suffix = FX_TO_TABLE_SUFFIX[self._fx_name_for(fx_id)]
            section = self._address_map.fx_start_section(fx_id, suffix)
            return self._address_map.value_range(
                section, f"{fx_type}{fx_id}{suffix}", option
            )
        section = self._address_map.start_section(fx_type, str(fx_id))
        return self._address_map.value_range(section, f"{fx_type}{fx_id}", option)
