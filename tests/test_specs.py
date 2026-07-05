"""Characterise the spec-table load, observed through AddressMap's interface.

These previously asserted on the six raw registries directly. Slice 6 made
those private to ``AddressMap``; the same load facts are now pinned through the
narrow accessors (block counts, value tables, chain lookup) and through the
observable decode path, not raw dict access.
"""


def test_all_fx_type_counts(gt):
    # Block counts derived from the Patch/Patch2/Patch3 tables at load time.
    expected = {
        "comp": 1,
        "dist": 2,
        "preamp": 2,
        "ns": 2,
        "eq": 4,
        "delay": 4,
        "mstDelay": 1,
        "chorus": 1,
        "fx": 4,
        "pedalFx": 1,
        "reverb": 1,
    }
    for fx_type, count in expected.items():
        assert gt._address_map.fx_block_count(fx_type) == count


def test_every_fx_type_resolves_to_a_value_table(gt):
    # Every declared fx_type resolves to a loaded, populated value table.
    for fx_type in gt.fx_types:
        table = gt._address_map.fx_value_table(fx_type)
        assert isinstance(table, dict) and table


def test_fx_sub_effect_tables_are_loaded(gt):
    # A resolved fx sub-effect maps to its PatchFx* value table.
    assert gt._address_map.fx_name_value_table("AGSim")
    assert gt._address_map.fx_name_value_table("Chorus")


def test_decode_path_is_wired(gt):
    # first_two_bytes / offset_in_patch_tables / last_byte_option are all
    # exercised by a successful decode of a known address.
    decoded = gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)
    assert decoded.name == "fx1"
    assert decoded.value_name == "SW"
    assert decoded.str_value == "ON"


def test_chain_element_lookup_round_trips(gt):
    # ChainElement is loaded as a bidirectional name<->int map.
    assert gt._address_map.chain_element_int("COMPRESSOR") == 0
    assert gt._address_map.chain_element_name(0) == "COMPRESSOR"
