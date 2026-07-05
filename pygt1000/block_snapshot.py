"""BlockSnapshot — assemble a block's whole presented state.

``BlockReader`` reads a *single* setting and ``Slider`` resolves a block's two
sliders, but neither owns "give me the full current state of this block." That
assembly — read SW, read TYPE (or synthesize a name for the ns/delay blocks that
have no TYPE field), record the fx block's resolved effect name through its
owner, ask ``Slider`` for the two sliders, and pack the dict — used to sit on
``GT1000`` as read-side orchestration, alongside the block iteration that scans
an fx type. It lives here now: one block per ``snapshot()``, and
``snapshot_all`` / ``snapshot_one`` for the iteration.

The collaborators are injected — ``block_reader`` for the setting reads,
``slider`` for slider resolution, ``set_fx_name(fx_id, name)`` to record the
fx-block name through its owner (``PatchState``), and ``address_map`` for the
block counts — so the assembly resolves against fakes with no wire. This is a
separate module rather than a method on ``BlockReader`` because ``Slider``
already depends on ``BlockReader.read_value``; folding the slider assembly back
into ``BlockReader`` would make that dependency circular.
"""

import logging

from .patch_state import FxBlock

logger = logging.getLogger(__name__)


class BlockSnapshot:
    def __init__(self, address_map, block_reader, slider, set_fx_name):
        self._address_map = address_map
        self._reader = block_reader
        self._slider = slider
        self._set_fx_name = set_fx_name

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
            slider1, slider2 = self._slider.sliders_for(fx_type, fx_id, name)
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
