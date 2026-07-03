"""The GT-1000 memory map and the forward (encode) path.

Historically the map knowledge was smeared across six parallel dicts built by
``GT1000._import_specs_tables`` and consumed by a fringe of one-line address
forwarders and ``if`` chains on ``GT1000``. ``AddressMap`` owns that load and
exposes a narrow encode interface:

- ``address_for(section, option, setting, value=None)`` — build an address,
  optionally with a trailing value byte.
- ``value_range(section, option, setting)`` — the numeric range for a setting.
- ``start_section`` / ``fx_start_section`` — which patch/patch2/patch3 section a
  block lives in, expressed as data rather than control flow.

The six registries (``_tables``, ``_first_two_bytes``, ``_offset_in_patch_tables``,
``_last_byte_option``, ``_fx_tables``, ``_fx_types_count``) are private to this
module. External readers go through narrow, intent-revealing accessors:
``fx_block_count`` / ``set_fx_block_count`` (block counts), ``fx_value_table`` /
``fx_name_value_table`` (a block's value table), and ``chain_element_name`` /
``chain_element_int`` (the ChainElement name<->int map).
"""

import json
import logging
from pathlib import Path

from .constants import TABLE_SUFFIX_TO_NAME

logger = logging.getLogger(__name__)


def bytes_to_int(value):
    return int.from_bytes(value, byteorder="big")


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


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
        self._tables = {}
        # Map the 2 MSBs of an address to a section.
        self._first_two_bytes = {}
        # Map an offset to its patch table (PatchFX, PatchEq, ...), one entry
        # for each of the 3 Patch container tables.
        self._offset_in_patch_tables = {}
        # Option entry for the last byte (ex: "SW"), one entry per Patch* table.
        self._last_byte_option = {}
        # fx_type -> its value table name (PatchComp, ...).
        self._fx_tables = {}
        # fx_type -> number of blocks of that type.
        self._fx_types_count = {}
        self._import_specs_tables()

    def _import_specs_tables(self):
        for i in self.fx_types:
            self._fx_types_count[i] = 0
        for i in (Path(__file__).parent / "specs").glob("*.json"):
            table_name = i.name.split(".")[0]
            table = json.loads(i.read_text())
            if table_name in ["Patch", "Patch2", "Patch3"]:
                if table_name not in self._offset_in_patch_tables:
                    self._offset_in_patch_tables[table_name] = {}
                for key in table:
                    self._offset_in_patch_tables[table_name][
                        table[key]["address"][1]
                    ] = (key, table[key]["table"])
                    if key in ["preampA", "preampB"]:
                        fx_type = "preamp"
                    else:
                        fx_type = "".join(i for i in key if not i.isdigit())
                    if fx_type not in self.fx_types:
                        continue
                    self._fx_types_count[fx_type] += 1
                    self._fx_tables[fx_type] = table[key]["table"]
            elif table_name == "base-addresses":
                for section in table:
                    msbs = [table[section]["address"][0], table[section]["address"][1]]
                    self._first_two_bytes[str(msbs)] = (section, table[section]["table"])
            elif table_name.startswith("Patch"):
                self._last_byte_option[table_name] = {}
                for option in table:
                    # Copy the whole entry so we can decide later if we only want
                    # the value or the name associated with the value.
                    self._last_byte_option[table_name][table[option]["offset"][1]] = (
                        option,
                        table[option],
                    )

            self._tables[table_name] = table

    # -- Narrow accessors (registries stay private) -------------------------

    def fx_block_count(self, fx_type):
        """Number of blocks of ``fx_type`` found at load time."""
        return self._fx_types_count[fx_type]

    def set_fx_block_count(self, fx_type, count):
        """Override a block count (GT-1000CORE has 3 fx blocks, not 4)."""
        self._fx_types_count[fx_type] = count

    def fx_value_table(self, fx_type):
        """The value table for a top-level block, keyed by its fx type."""
        return self._tables[self._fx_tables[fx_type]]

    def fx_name_value_table(self, table_suffix):
        """The value table for a resolved fx sub-effect, keyed by the PatchFx
        table suffix (ex: ``AGSim`` -> ``PatchFxAGSim``)."""
        return self._tables[f"PatchFx{table_suffix}"]

    def chain_element_name(self, int_value):
        """Chain element name for a raw chain byte (ex: 0 -> ``COMPRESSOR``)."""
        return self._tables["ChainElement"][str(int_value)]

    def chain_element_int(self, name):
        """Raw chain byte for a chain element name (ex: ``COMPRESSOR`` -> 0)."""
        return int(self._tables["ChainElement"][name])

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
        if section not in self._tables["base-addresses"]:
            logger.error(f"Entry {section} missing in base-addresses")
            return None

        section_entry = self._tables["base-addresses"][section]
        address = bytes_to_int(section_entry["address"])

        if option not in self._tables[section_entry["table"]]:
            logger.error(f"{option} not in section table")
            return None
        option_entry = self._tables[section_entry["table"]][option]
        option_address_offset = bytes_to_int(option_entry["address"])

        if setting not in self._tables[option_entry["table"]]:
            logger.error(f"{setting} not in option_entry")
            logger.debug(f"entries: {self._tables[option_entry['table']].keys()}")
            return None
        setting_entry = self._tables[option_entry["table"]][setting]
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
        if section not in self._tables["base-addresses"]:
            logger.error(f"Entry {section} missing in base-addresses")
            return None

        section_entry = self._tables["base-addresses"][section]

        if option not in self._tables[section_entry["table"]]:
            logger.error(f"Entry {option} not in section table")
            return None
        option_entry = self._tables[section_entry["table"]][option]

        setting_entry = self._tables[option_entry["table"]][setting]
        return setting_entry["value_range"]

    def decode(self, address, value):
        """Reverse of ``address_for``: an address + raw value -> semantic dict.

        Returns ``None`` for a malformed address, a list value, or an address
        that maps to no known section/table entry. When the block is an fx
        sub-effect the fx table suffix is resolved to its display name.
        """
        ret = {}
        if len(address) != 4:
            logger.error(f"Unknown address format received {address}")
            return None
        if isinstance(value, list):
            logger.error(f"value format must be int, received {value}")
            return None
        msbs = str([address[0], address[1]])
        if msbs not in self._first_two_bytes:
            logger.info(f"Data received for unknown address {bytes_as_hex(address)}")
            return None
        section, table = self._first_two_bytes[msbs]
        if table not in self._offset_in_patch_tables:
            return None
        if address[2] not in self._offset_in_patch_tables[table]:
            return None
        name, patch_table = self._offset_in_patch_tables[table][address[2]]
        if patch_table not in self._last_byte_option:
            return None
        if address[3] not in self._last_byte_option[patch_table]:
            return None
        value_name, value_entry = self._last_byte_option[patch_table][address[3]]
        str_value = None
        for i in value_entry["values"]:
            if value_entry["values"][i] == value:
                str_value = i
        ret["section"] = section
        ret["table"] = table
        ret["name"] = name
        ret["patch_table"] = patch_table
        ret["value_name"] = value_name
        ret["str_value"] = str_value
        ret["int_value"] = value

        # Now check if it's an fx_type and extract its fx_id
        fx_type = None
        fx_id = None
        for i in self.fx_types:
            if name.startswith(i):
                fx_type = i
                if name == i:
                    fx_id = ""
        if fx_type is None:
            return ret
        if fx_id is None:
            if fx_type == "preamp" and name == "preampA":
                fx_id = "1"
            elif fx_type == "preamp" and name == "preampB":
                fx_id = "2"
            elif fx_type == "fx":
                fx_id = name[2]
            else:
                fx_id = name.replace(fx_type, "")
        if fx_type == "fx" and len(name) > 3:
            fx_name = name[3:]
            ret["fx_table_suffix"] = fx_name
            if fx_name in TABLE_SUFFIX_TO_NAME:
                ret["fx_name"] = TABLE_SUFFIX_TO_NAME[fx_name]
        ret["fx_type"] = fx_type
        ret["fx_id"] = fx_id
        return ret

    def value_for(self, fx_type, prop, value_name):
        """The raw int for a named value under an fx type's ``prop`` table."""
        table_name = self.fx_type_table_name(fx_type)
        if table_name is None:
            logger.error(f"{fx_type} not found in tables")
            return None
        if prop not in self._tables[table_name]:
            logger.error(f"{prop} not found in {self._tables[table_name].keys()}")
            return None
        if "values" not in self._tables[table_name][prop]:
            logger.error("No 'values' field in table {table_name} {prop}")
            return None
        for name in self._tables[table_name][prop]["values"]:
            if name == value_name:
                return self._tables[table_name][prop]["values"][name]
        return None

    def types_for(self, fx_type):
        """The selectable TYPE names for an fx type, minus the four bass
        variants that are excluded from the public list."""
        if fx_type in ["ns", "delay"]:
            return []
        table_name = self.fx_type_table_name(fx_type)
        if table_name not in self._tables:
            return None
        return [
            x
            for x in self._tables[table_name]["TYPE"]["values"].keys()
            if x
            not in ["DEFRETTER BASS", "OCTAVE BASS", "SLOW GEAR BASS", "TOUCH WAH BASS"]
        ]

    def _sliceindex(self, x):
        i = 0
        for c in x:
            if c.isalpha():
                i = i + 1
                return i
            i = i + 1

    def _upperfirst(self, x):
        i = self._sliceindex(x)
        return x[:i].upper() + x[i:]

    def fx_type_table_name(self, fx_type):
        return f"Patch{self._upperfirst(fx_type)}"
