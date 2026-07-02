"""Characterise the spec-table load (``_import_specs_tables``).

These lock the shape of the six parallel registries the loader builds, so the
planned collapse into a single Address Map module can be verified equivalent.
"""


def test_all_fx_type_counts(gt):
    # Block counts derived from the Patch/Patch2/Patch3 tables at load time.
    assert gt.fx_types_count == {
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


def test_fx_tables_map_every_type(gt):
    assert gt.fx_tables == {
        "fx": "PatchFx",
        "comp": "PatchComp",
        "dist": "PatchDist",
        "preamp": "PatchPreamp",
        "ns": "PatchNs",
        "eq": "PatchEq",
        "delay": "PatchDelay",
        "mstDelay": "PatchMstDelay",
        "chorus": "PatchChorus",
        "reverb": "PatchReverb",
        "pedalFx": "PatchPedalFx",
    }
    # Every declared fx_type resolves to a loaded table.
    for fx_type, table_name in gt.fx_tables.items():
        assert table_name in gt.tables


def test_registries_are_populated(gt):
    # base-addresses -> first_two_bytes lookup for the reverse (decode) path.
    assert gt.first_two_bytes
    # The three Patch container tables each get an offset map.
    assert set(gt.offset_in_patch_tables) == {"Patch", "Patch2", "Patch3"}
    # last_byte_option holds one entry per Patch* table (never the containers).
    assert gt.last_byte_option
    assert "Patch" not in gt.last_byte_option
    assert "PatchFx" in gt.last_byte_option


def test_core_tables_present(gt):
    for name in ["base-addresses", "Patch", "Patch2", "Patch3", "ChainElement", "PatchFx"]:
        assert name in gt.tables
