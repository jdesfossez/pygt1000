from pygt1000 import GT1000


def test_create_object():
    gt = GT1000()
    assert gt.device_id == 0x7F


def test_open_tables():
    gt = GT1000()
    expected_tables = [
        "PatchFxCVibe",
        "PatchFx",
        "PatchFxSBend",
        "PatchFxTremolo",
        "PatchFxPitchShift",
        "PatchFxDefretter",
        "PatchFxTWah",
        "PatchChorus",
        "PatchFxAWah",
        "PatchFxOctaveBass",
        "PatchFxPan",
        "PatchFxOvertone",
        "PatchFxAGSim",
        "PatchFxRingMod",
        "PatchReverb",
        "PatchComp",
        "PatchFxSitarSim",
        "PatchFxHarmonist",
        "Patch3",
        "PatchFxFlanger",
        "Patch2",
        "PatchFxChorus",
        "PatchFxChorusBass",
        "PatchDelay",
        "PatchDist",
        "PatchMstDelay",
        "PatchFxSlowGearBass",
        "PatchFxSlowGear",
        "PatchFxComp",
        "PatchFxMasteringFx",
        "PatchFxTWahBass",
        "PatchFxFeedbacker",
        "PatchPedalFx",
        "PatchFxSlicer",
        "PatchFxSoundHold",
        "PatchNs",
        "PatchFxDist",
        "PatchFxOctave",
        "PatchPreamp",
        "PatchFxRotary",
        "PatchFxFlangerBass",
        "PatchFxVibrato",
        "Patch",
        "PatchFxHumanizer",
        "PatchFxAcReso",
        "PatchFxPhaser",
        "base-addresses",
        "PatchFxDefretterBass",
        "PatchEq",
    ]
    for i in expected_tables:
        assert i in gt.tables


def test_build_message():
    gt = GT1000()

    start_section = gt._get_start_section("fx", "1")
    assert start_section == "patch (temporary patch)"
    offset = gt._construct_address_value(start_section, "fx1", "TYPE", None)
    assert offset == [0x10, 0x0, 0x23, 0x1]

    start_section = gt._get_start_section("fx", "4")
    assert start_section == "patch3 (temporary patch)"
    offset = gt._construct_address_value(start_section, "fx4", "TYPE", None)
    assert offset == [0x10, 0x2, 0x1, 0x1]


def test_set_value_message():
    gt = GT1000()

    start_section = gt._get_start_section("fx", "1")
    message = gt.build_dt_message(start_section, "fx1", "SW", "ON")
    assert message == [
        0xF0,
        0x41,
        0x7F,
        0x0,
        0x0,
        0x0,
        0x4F,
        0x12,
        0x10,
        0x0,
        0x23,
        0x0,
        0x1,
        0x4C,
        0xF7,
    ]

    start_section = gt._get_start_section("fx", "4")
    message = gt.build_dt_message(start_section, "fx4", "TYPE", "CHORUS")
    assert message == [
        0xF0,
        0x41,
        0x7F,
        0x0,
        0x0,
        0x0,
        0x4F,
        0x12,
        0x10,
        0x2,
        0x1,
        0x1,
        0x3,
        0x69,
        0xF7,
    ]

def test_value_lookup():
    gt = GT1000()
    assert gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)["name"] == "fx1"
    assert gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)["value_name"] == "SW"
    assert gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)["str_value"] == "ON"
    assert gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)["fx_type"] == "fx"
    assert gt.lookup([0x10, 0x0, 0x23, 0x0], 0x1)["fx_id"] == "1"

    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["name"] == "fx1AGSim"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["value_name"] == "BODY"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["fx_type"] == "fx"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["fx_id"] == "1"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["fx_table_suffix"] == "AGSim"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["patch_table"] == "PatchFxAGSim"
    assert gt.lookup([0x10, 0x0, 0x24, 0x0], 0x1)["fx_name"] == "AC GUITAR SIM"
