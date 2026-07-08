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


# ---- span + named-tail value lists (delay TIME et al) ------------------------
# The option tables list e.g. "1ms - 2000ms, 32ndNote, ..." for TIME (1-2018):
# values 1..2000 are numeric milliseconds and the named notes occupy the LAST
# 18 slots (2001..2018). The extractor used to enumerate the tail from 2.

def _spec(name):
    import json
    from pathlib import Path
    p = Path(__file__).parent.parent / "pygt1000" / "specs" / f"{name}.json"
    with p.open() as f:
        return json.load(f)


def test_delay_time_named_tail_starts_after_ms_span():
    time = _spec("PatchDelay")["TIME"]
    assert time["value_range"] == [1, 2018]
    assert time["values"]["32ndNote"] == 2001
    assert time["values"]["DoubleWholeNote"] == 2018
    assert "1ms - 2000ms" not in time["values"]


def test_mst_delay_time_named_tail():
    time = _spec("PatchMstDelay")["TIME"]
    assert time["values"]["32ndNote"] == time["value_range"][1] - 18 + 1


def test_system_tables_extracted():
    sc = _spec("SystemCommon")
    assert sc["Patch Number"]["bytes"] == 4
    assert sc["Patch Number"]["value_range"] == [0, 499]
    assert sc["METRONOME BPM"]["bytes"] == 4
    assert sc["TUNER MODE"]["values"] == {"NORMAL": 0, "STREAM": 1}
    se = _spec("SystemEfct")
    assert "PHRASE LOOP:REC ACTION" in se
    assert se["METRONOME LEVEL"]["value_range"] == [0, 100]


def test_consumed_base_addresses_reference_existing_tables():
    # Not every base-addresses section has a transcribed table yet (e.g.
    # "control" -> SystemControl2 is still todo); assert only the sections the
    # library and downstream consumers actually resolve today.
    import json
    from pathlib import Path
    specs = Path(__file__).parent.parent / "pygt1000" / "specs"
    base = json.loads((specs / "base-addresses.json").read_text())
    for section in ("patch (temporary patch)", "common", "efct", "inout"):
        table = base[section]["table"]
        assert (specs / f"{table}.json").exists(), (
            f"{section} references missing table {table}")
