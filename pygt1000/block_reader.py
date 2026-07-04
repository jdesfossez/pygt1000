"""BlockReader — the read-side pipeline for a block's current state.

"Read a block's current value from the device" is one job: build the address for
a ``(block, setting)``, fetch the byte over MIDI, and decode it to the label the
spec gives that byte (or the raw byte when there is no mapping). It used to be
smeared across two near-identical readers on ``GT1000`` with the byte->label loop
copied verbatim between them; here it lives once, and the decode is delegated to
``AddressMap.read_label`` so it shares the reverse mapping the inbound
``AddressMap.decode`` path already uses.

The device read (``fetch``) is injected — ``fetch(offset, length)`` returns the
reply data (a list whose first byte is the value) or ``None`` — so the module
resolves against a fake reader with no wire, the same pattern ``Slider`` uses.
This is the read-side counterpart to ``PatchState`` (the write/model side); it
builds on ``AddressMap.address_for_block`` rather than re-deriving addresses.
"""

import logging

from .constants import ONE_BYTE, FX_TO_TABLE_SUFFIX

logger = logging.getLogger(__name__)


class BlockReader:
    def __init__(self, address_map, fetch, fx_name_for):
        """``address_map`` builds addresses and decodes bytes;
        ``fetch(offset, length)`` reads device memory (or returns ``None``);
        ``fx_name_for(fx_id)`` resolves the current effect name for an fx
        block, read live so it follows the device."""
        self._address_map = address_map
        self._fetch = fetch
        self._fx_name_for = fx_name_for

    def read(self, fx_type, fx_id, setting, just_range=False):
        """A top-level block's ``setting``: the decoded label, or — with
        ``just_range`` — the raw byte (used when only the numeric value is
        wanted). ``None`` when the device does not answer."""
        offset = self._address_map.address_for_block(fx_type, fx_id, setting)
        raw = self._fetch_byte(offset)
        if raw is None:
            logger.warning(f"no data for {fx_type}{fx_id} {setting}")
            return None
        if just_range:
            return raw
        table = self._address_map.fx_value_table(fx_type)
        return self._address_map.read_label(table, setting, raw)

    def read_fx(self, fx_type, fx_id, setting):
        """An fx block's ``setting``, resolved through its current sub-effect
        table (so the byte->label map matches the loaded effect). ``None`` when
        the address cannot be resolved or the device does not answer."""
        fx_name = self._fx_name_for(fx_id)
        logger.info(f"FX_VALUE for {fx_name}, {fx_type}{fx_id}, {setting}")
        offset = self._address_map.address_for_block(
            fx_type, fx_id, setting, fx_name=fx_name
        )
        if offset is None:
            return None
        raw = self._fetch_byte(offset)
        if raw is None:
            logger.warning(f"no data for {fx_type}{fx_id} {fx_name} {setting}")
            return None
        table = self._address_map.fx_name_value_table(FX_TO_TABLE_SUFFIX[fx_name])
        return self._address_map.read_label(table, setting, raw)

    def read_value(self, fx_type, fx_id, option):
        """A param's current value for Slider: the fx block resolves through its
        sub-effect table (label or raw); every other block reads the raw byte."""
        if fx_type == "fx":
            return self.read_fx(fx_type, fx_id, option)
        return self.read(fx_type, fx_id, option, just_range=True)

    def _fetch_byte(self, offset):
        data = self._fetch(offset, ONE_BYTE)
        if data is None:
            return None
        return data[0]
