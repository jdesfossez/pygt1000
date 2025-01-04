#!/usr/bin/env python

# Example script to read and write the current chain.
# There are two formats to read/write the chain:
#  - a list of words taken from the "CHAIN ELEMENT TABLE" section of the spec
#  - a list of EffectBlock and DividerBlock objects (which makes the
#    represention of branches a bit clearer)
#
# Below are examples on how to read the current chain in both formats and how
# to recreate the first effect chain of the GT-1000CORE (PREMIUM DRIVE) in
# both formats.

from pygt1000.gt1000 import (
    GT1000,
)

from pygt1000.chain import (
    EffectBlock,
    DividerBlock,
)

gt1000 = GT1000()

gt1000.open_ports()

# Read the current chain as words
txt_chain = gt1000.read_chain()
print(f"Chain in the text format: {txt_chain}")

# Read the chain as objects:
print(f"Chain in the object format: {gt1000.parse_chain(txt_chain)}")

# Manually write a chain from text
my_txt_chain = [
    "PEDALFX",
    "COMPRESSOR",
    "EQUALIZER3",
    "FX1",
    "FX2",
    "DIVIDER1",
    "DISTORTION1",
    "SEND/RETURN1",
    "AIRDPREAMP1",
    "NOISESUPPRESSOR1",
    "EQUALIZER1",
    "BRANCHSPLIT1",
    "DISTORTION2",
    "SEND/RETURN2",
    "AIRDPREAMP2",
    "NOISESUPPRESSOR2",
    "EQUALIZER2",
    "MIXER1",
    "EQUALIZER4",
    "FOOTVOLUME",
    "DELAY1",
    "DELAY2",
    "DELAY3",
    "DELAY4",
    "MASTERDELAY",
    "FX3",
    "FX4",
    "CHORUS",
    "LOOPER",
    "REVERB",
    "DIVIDER2",
    "BRANCHSPLIT2",
    "MIXER2",
    "DIVIDER3",
    "BRANCHSPLIT3",
    "MIXER3",
    "BYPASSMAINR",
    "MAINSP.SIMULATORL",
    "MAINSP.SIMULATORR",
    "BYPASSMAINL",
    "MAINOUTL",
    "MAINOUTR",
    "BYPASSSUBR",
    "SUBSP.SIMULATORL",
    "SUBSP.SIMULATORR",
    "BYPASSSUBL",
    "SUBOUTL",
    "SUBOUTR",
    "(RESERVED)",
]
gt1000.write_chain_from_txt(my_txt_chain)

# Manually write a chain from objects
my_obj_chain = [
    EffectBlock("PEDALFX"),
    EffectBlock("COMPRESSOR"),
    EffectBlock("EQUALIZER3"),
    EffectBlock("FX1"),
    EffectBlock("FX2"),
    DividerBlock(
        divider="DIVIDER1",
        branch_a=[
            EffectBlock("DISTORTION1"),
            EffectBlock("SEND/RETURN1"),
            EffectBlock("AIRDPREAMP1"),
            EffectBlock("NOISESUPPRESSOR1"),
            EffectBlock("EQUALIZER1"),
        ],
        branch_b=[
            EffectBlock("DISTORTION2"),
            EffectBlock("SEND/RETURN2"),
            EffectBlock("AIRDPREAMP2"),
            EffectBlock("NOISESUPPRESSOR2"),
            EffectBlock("EQUALIZER2"),
        ],
        mixer="MIXER1",
    ),
    EffectBlock("EQUALIZER4"),
    EffectBlock("FOOTVOLUME"),
    EffectBlock("DELAY1"),
    EffectBlock("DELAY2"),
    EffectBlock("DELAY3"),
    EffectBlock("DELAY4"),
    EffectBlock("MASTERDELAY"),
    EffectBlock("FX3"),
    EffectBlock("FX4"),
    EffectBlock("CHORUS"),
    EffectBlock("LOOPER"),
    EffectBlock("REVERB"),
    DividerBlock(divider="DIVIDER2", branch_a=[], branch_b=[], mixer="MIXER2"),
    DividerBlock(divider="DIVIDER3", branch_a=[], branch_b=[], mixer="MIXER3"),
    EffectBlock("BYPASSMAINR"),
    EffectBlock("MAINSP.SIMULATORL"),
    EffectBlock("MAINSP.SIMULATORR"),
    EffectBlock("BYPASSMAINL"),
    EffectBlock("MAINOUTL"),
    EffectBlock("MAINOUTR"),
    EffectBlock("BYPASSSUBR"),
    EffectBlock("SUBSP.SIMULATORL"),
    EffectBlock("SUBSP.SIMULATORR"),
    EffectBlock("BYPASSSUBL"),
    EffectBlock("SUBOUTL"),
    EffectBlock("SUBOUTR"),
    EffectBlock("(RESERVED)"),
]
gt1000.write_chain_from_obj(my_obj_chain)

gt1000.close_ports()
