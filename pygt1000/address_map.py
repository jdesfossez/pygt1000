"""The GT-1000 memory map and the forward (encode) path.

Historically the map knowledge was smeared across six parallel dicts built by
``GT1000._import_specs_tables`` and consumed by ``_construct_address_value`` /
``_lookup_value_range`` plus the ``_get_start_section`` / ``_get_fx_start_section``
``if`` chains. ``AddressMap`` owns that load and exposes a narrow encode
interface:

- ``address_for(section, option, setting, value=None)`` — build an address,
  optionally with a trailing value byte.
- ``value_range(section, option, setting)`` — the numeric range for a setting.
- ``start_section`` / ``fx_start_section`` — which patch/patch2/patch3 section a
  block lives in, expressed as data rather than control flow.

The six registries (``tables``, ``first_two_bytes``, ``offset_in_patch_tables``,
``last_byte_option``, ``fx_tables``, ``fx_types_count``) are attributes of this
module; ``GT1000`` keeps backward-compatible accessors that delegate here.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def bytes_to_int(value):
    return int.from_bytes(value, byteorder="big")


class AddressMap:
    # The three temporary-patch sections a block can be routed to.
    PATCH_SECTION = "patch (temporary patch)"
    PATCH2_SECTION = "patch2 (temporary patch)"
    PATCH3_SECTION = "patch3 (temporary patch)"

    # Section routing for fx sub-effect tables, as data. Any fx table suffix not
    # listed here (for fx id 1-3) lives in the base patch section; fx id 4 always
    # routes to patch3 (see ``fx_start_section``).
    _FX_SECTION_BY_SUFFIX = {
        "ChorusBass": PATCH2_SECTION,
        "FlangerBass": PATCH2_SECTION,
        "Dist": PATCH3_SECTION,
        "MasterFx": PATCH3_SECTION,
    }

    def __init__(self, fx_types):
        self.fx_types = fx_types
        # Loaded spec tables keyed by file name (PatchFx, base-addresses, ...).
        self.tables = {}
        # Map the 2 MSBs of an address to a section.
        self.first_two_bytes = {}
        # Map an offset to its patch table (PatchFX, PatchEq, ...), one entry
        # for each of the 3 Patch container tables.
        self.offset_in_patch_tables = {}
        # Option entry for the last byte (ex: "SW"), one entry per Patch* table.
        self.last_byte_option = {}
        # fx_type -> its value table name (PatchComp, ...).
        self.fx_tables = {}
        # fx_type -> number of blocks of that type.
        self.fx_types_count = {}
        self._import_specs_tables()

    def _import_specs_tables(self):
        for i in self.fx_types:
            self.fx_types_count[i] = 0
        for i in (Path(__file__).parent / "specs").glob("*.json"):
            table_name = i.name.split(".")[0]
            table = json.loads(i.read_text())
            if table_name in ["Patch", "Patch2", "Patch3"]:
                if table_name not in self.offset_in_patch_tables:
                    self.offset_in_patch_tables[table_name] = {}
                for key in table:
                    self.offset_in_patch_tables[table_name][
                        table[key]["address"][1]
                    ] = (key, table[key]["table"])
                    if key in ["preampA", "preampB"]:
                        fx_type = "preamp"
                    else:
                        fx_type = "".join(i for i in key if not i.isdigit())
                    if fx_type not in self.fx_types:
                        continue
                    self.fx_types_count[fx_type] += 1
                    self.fx_tables[fx_type] = table[key]["table"]
            elif table_name == "base-addresses":
                for section in table:
                    msbs = [table[section]["address"][0], table[section]["address"][1]]
                    self.first_two_bytes[str(msbs)] = (section, table[section]["table"])
            elif table_name.startswith("Patch"):
                self.last_byte_option[table_name] = {}
                for option in table:
                    # Copy the whole entry so we can decide later if we only want
                    # the value or the name associated with the value.
                    self.last_byte_option[table_name][table[option]["offset"][1]] = (
                        option,
                        table[option],
                    )

            self.tables[table_name] = table

    def start_section(self, fx_type, fx_id):
        """Section for a top-level block. fx id 4 lives in patch3."""
        if not isinstance(fx_id, str):
            logger.error("fx_id should be a string")
            fx_id = str(fx_id)
        if fx_type == "fx" and fx_id == "4":
            return self.PATCH3_SECTION
        return self.PATCH_SECTION

    def fx_start_section(self, fx_id, table_suffix):
        """Section for an fx sub-effect table, keyed by its table suffix."""
        if str(fx_id) == "4":
            return self.PATCH3_SECTION
        return self._FX_SECTION_BY_SUFFIX.get(table_suffix, self.PATCH_SECTION)

    def address_for(self, section, option, setting, value=None):
        """Build the address bytes for ``setting`` under ``section``/``option``.

        With ``value`` left as ``None`` only the address is returned; otherwise
        the encoded value byte is appended.
        """
        if section not in self.tables["base-addresses"]:
            logger.error(f"Entry {section} missing in base-addresses")
            return None

        section_entry = self.tables["base-addresses"][section]
        address = bytes_to_int(section_entry["address"])

        if option not in self.tables[section_entry["table"]]:
            logger.error(f"{option} not in section table")
            return None
        option_entry = self.tables[section_entry["table"]][option]
        option_address_offset = bytes_to_int(option_entry["address"])

        if setting not in self.tables[option_entry["table"]]:
            logger.error(f"{setting} not in option_entry")
            logger.debug(f"entries: {self.tables[option_entry['table']].keys()}")
            return None
        setting_entry = self.tables[option_entry["table"]][setting]
        setting_address_offset = bytes_to_int(setting_entry["offset"])

        address += option_address_offset + setting_address_offset
        if value is None:
            num_bytes = (address.bit_length() + 7) // 8
            byte_sequence = address.to_bytes(num_bytes, byteorder="big")
            return [byte for byte in byte_sequence]

        # If we set the raw value
        if value not in setting_entry["values"]:
            value_byte = value.to_bytes(1, byteorder="big")
        else:
            param_entry = setting_entry["values"][value]
            value_byte = param_entry.to_bytes(1, byteorder="big")

        num_bytes = (address.bit_length() + 7) // 8
        byte_sequence = address.to_bytes(num_bytes, byteorder="big") + value_byte
        return [byte for byte in byte_sequence]

    def value_range(self, section, option, setting):
        if section not in self.tables["base-addresses"]:
            logger.error(f"Entry {section} missing in base-addresses")
            return None

        section_entry = self.tables["base-addresses"][section]

        if option not in self.tables[section_entry["table"]]:
            logger.error(f"Entry {option} not in section table")
            return None
        option_entry = self.tables[section_entry["table"]][option]

        setting_entry = self.tables[option_entry["table"]][setting]
        return setting_entry["value_range"]
