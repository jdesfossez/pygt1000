"""BlockSnapshot — read a block's whole presented state.

"Give me the full current state of this block" is one job: read SW, read TYPE
(or synthesize a name for the ns/delay blocks that have no TYPE field), record an
fx block's resolved effect name through its owner, resolve the block's two
sliders, and pack an :class:`~pygt1000.patch_state.FxBlock` — plus the block
iteration that scans an fx type. The slider half of that job carries its own
knowledge: the *policy* (which two params a block exposes, the eq special case)
and the *resolution* (a param's range from the AddressMap plus its current value
over MIDI).

This used to be two modules — ``Slider`` (policy + resolution) and
``BlockSnapshot`` (assembly) — held apart because ``Slider`` depended on
``BlockReader.read_value`` and folding the assembly back into ``BlockReader``
would have made that dependency circular. That split existed only to break the
cycle: policy/resolution and assembly are the two halves of one read, so they
live together here, above the ``BlockReader`` mechanism (address -> fetch ->
decode) they both build on. The mechanism stays its own module — it is a genuine
seam, not a cycle artifact — so the read path is a deliberate two: mechanism
below, block-read above.

The collaborators are injected — ``block_reader`` for the setting/value reads
(so the whole read resolves against a fake reader with no wire), ``fx_name_for``
for an fx block's currently-resolved effect name, ``set_fx_name`` to record that
name through its owner (``PatchState``), and ``address_map`` for ranges and block
counts.
"""

import logging

from .constants import FX_TO_TABLE_SUFFIX
from .patch_state import FxBlock, SliderValue

logger = logging.getLogger(__name__)


class BlockSnapshot:
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

    def __init__(self, address_map, block_reader, fx_name_for, set_fx_name):
        """``address_map`` supplies value ranges and block counts;
        ``block_reader`` reads a setting (``read``) or a param's current value
        (``read_value``); ``fx_name_for(fx_id)`` returns the currently-resolved
        effect name for an fx block (read live, so it follows the device);
        ``set_fx_name(fx_id, name)`` records that name through its owner."""
        self._address_map = address_map
        self._reader = block_reader
        self._fx_name_for = fx_name_for
        self._set_fx_name = set_fx_name

    # ------------------------------------------------------------------
    # Whole-block assembly
    # ------------------------------------------------------------------

    def snapshot(self, fx_type, fx_id, get_sliders=True):
        """The full presented state of one block as an :class:`FxBlock`:
        ``state`` (SW), ``name``, and — with ``get_sliders`` — both sliders. The
        ns/delay blocks have no TYPE field, so their name is synthesized; the fx
        block's resolved effect name is recorded through its owner so slider
        layout and later reads resolve it. With ``get_sliders=False`` the two
        slider fields are left at their default None (they were simply absent
        from the old dict shape)."""
        state = self._reader.read(fx_type, fx_id, "SW")
        # These don't have a TYPE field in the spec.
        if fx_type in ("ns", "delay"):
            name = f"{fx_type}{fx_id}"
        else:
            name = self._reader.read(fx_type, fx_id, "TYPE")
        if fx_type == "fx":
            self._set_fx_name(fx_id, name)
        if get_sliders:
            slider1, slider2 = self.sliders_for(fx_type, fx_id, name)
            return FxBlock(
                fx_id=fx_id,
                state=state,
                name=name,
                slider1=slider1,
                slider2=slider2,
            )
        return FxBlock(fx_id=fx_id, state=state, name=name)

    def snapshot_all(self, fx_type):
        """Snapshot every block of ``fx_type`` in address order."""
        out = []
        for i in range(self._address_map.fx_block_count(fx_type)):
            fx_type, fx_id = self._address_map.normalize_block(fx_type, i + 1)
            out.append(self.snapshot(fx_type, fx_id))
        return out

    def snapshot_one(self, fx_type, fx_id, get_sliders=True):
        """Snapshot a single block of ``fx_type`` by id (a falsy ``fx_id`` takes
        the first block); ``None`` when no block matches."""
        for i in range(self._address_map.fx_block_count(fx_type)):
            fx_type, _fx_id = self._address_map.normalize_block(fx_type, i + 1)
            if not fx_id:
                return self.snapshot(fx_type, _fx_id, get_sliders)
            elif fx_id == _fx_id:
                return self.snapshot(fx_type, _fx_id, get_sliders)
        return None

    # ------------------------------------------------------------------
    # Slider policy + resolution
    # ------------------------------------------------------------------

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
        value = self._reader.read_value(fx_type, fx_id, option)
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
