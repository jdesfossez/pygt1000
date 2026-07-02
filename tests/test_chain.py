"""Characterise the signal-chain parser/serializer in ``pygt1000.chain``.

This module is pure (token list <-> block tree) and was previously untested.
The roundtrip property is the safety net for any future rework of the chain.
"""

from pygt1000.chain import (
    EffectBlock,
    DividerBlock,
    parse_chain,
    serialize_chain,
)


def test_parse_flat_chain_is_all_effect_blocks():
    tokens = ["PEDALFX", "COMPRESSOR", "EQUALIZER3", "AMP1"]
    blocks = parse_chain(list(tokens))
    assert all(isinstance(b, EffectBlock) for b in blocks)
    assert [b.name for b in blocks] == tokens


def test_parse_divider_splits_into_branches():
    tokens = ["DIVIDER1", "EQUALIZER1", "BRANCHSPLIT1", "DELAY1", "MIXER1"]
    (block,) = parse_chain(list(tokens))
    assert isinstance(block, DividerBlock)
    assert block.divider_name == "DIVIDER1"
    assert [b.name for b in block.branch_a] == ["EQUALIZER1"]
    assert [b.name for b in block.branch_b] == ["DELAY1"]
    assert block.mixer_name == "MIXER1"


def test_divider_surrounded_by_effects():
    tokens = [
        "PEDALFX", "COMPRESSOR",
        "DIVIDER1", "EQUALIZER1", "BRANCHSPLIT1", "DELAY1", "MIXER1",
        "REVERB",
    ]
    blocks = parse_chain(list(tokens))
    assert [type(b) for b in blocks] == [EffectBlock, EffectBlock, DividerBlock, EffectBlock]


def _roundtrip(tokens):
    return serialize_chain(parse_chain(list(tokens))) == tokens


def test_roundtrip_flat():
    assert _roundtrip(["PEDALFX", "COMPRESSOR", "EQUALIZER3", "AMP1"])


def test_roundtrip_with_divider():
    assert _roundtrip(
        ["PEDALFX", "DIVIDER1", "EQUALIZER1", "BRANCHSPLIT1", "DELAY1", "MIXER1", "REVERB"]
    )


def test_roundtrip_nested_dividers():
    tokens = [
        "DIVIDER1",
        "DIVIDER2", "CHORUS1", "BRANCHSPLIT2", "DELAY1", "MIXER2",
        "BRANCHSPLIT1",
        "REVERB1",
        "MIXER1",
    ]
    assert _roundtrip(tokens)


def test_serialize_effect_block():
    assert serialize_chain([EffectBlock("REVERB")]) == ["REVERB"]
