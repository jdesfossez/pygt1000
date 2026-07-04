from pygt1000 import GT1000
from pygt1000.transport import FakeTransport


def test_create_object():
    gt = GT1000()
    assert gt.device_id == 0x7F


def test_open_tables():
    # The spec load populated the AddressMap interface: every fx type resolves
    # to a value table, a representative sub-effect resolves to its PatchFx*
    # table, and the ChainElement map is loaded. (Table names are an AddressMap
    # internal now; this pins the observable result of the load instead.)
    gt = GT1000()
    for fx_type in gt.fx_types:
        assert gt._address_map.fx_value_table(fx_type)
    assert gt._address_map.fx_name_value_table("AGSim")
    assert gt._address_map.chain_element_name(0) == "COMPRESSOR"


def test_build_message():
    gt = GT1000()

    start_section = gt._address_map.start_section("fx", "1")
    assert start_section == "patch (temporary patch)"
    offset = gt._address_map.address_for(start_section, "fx1", "TYPE", None)
    assert offset == [0x10, 0x0, 0x23, 0x1]

    start_section = gt._address_map.start_section("fx", "4")
    assert start_section == "patch3 (temporary patch)"
    offset = gt._address_map.address_for(start_section, "fx4", "TYPE", None)
    assert offset == [0x10, 0x2, 0x1, 0x1]


def test_set_value_message():
    gt = GT1000()

    start_section = gt._address_map.start_section("fx", "1")
    address_value = gt._address_map.address_for(start_section, "fx1", "SW", "ON")
    message = gt._codec.encode_dt1(gt.device_id, address_value)
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

    start_section = gt._address_map.start_section("fx", "4")
    address_value = gt._address_map.address_for(start_section, "fx4", "TYPE", "CHORUS")
    message = gt._codec.encode_dt1(gt.device_id, address_value)
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


def test_get_all_fx_types():
    gt = GT1000()
    all_types = gt.get_all_fx_types("fx")
    assert "CHORUS" in all_types

    for fx_type in gt.fx_types:
        if fx_type in ["ns", "delay"]:
            continue
        assert len(gt.get_all_fx_types(fx_type)) > 0


def test_get_value_from_value_name():
    gt = GT1000()
    value = gt.get_fx_value_from_value_name("fx", "TYPE", "CHORUS")
    assert value == 3


# --------------------------------------------------------------------------
# Keepalive wiring: one thread model, injected reopen policy
# --------------------------------------------------------------------------

def test_start_refresh_thread_runs_one_thread_model_that_stops_cleanly():
    # The bespoke check_alive_thread is gone; the keepalive runs under the
    # KeepAlive worker alongside the RefreshScheduler, and both stop together.
    gt = GT1000(transport=FakeTransport())
    assert not hasattr(gt, "check_alive_thread")

    gt.start_refresh_thread()
    gt.stop_refresh_thread()

    assert gt._keepalive.join(timeout=2.0), "keepalive worker did not exit"
    assert gt._refresh.join(timeout=2.0), "refresh worker did not exit"


def test_reopen_ports_policy_closes_then_reopens():
    # The reopen policy the keepalive signals is owned by GT1000: on an
    # unresponsive device it closes the ports, then reopens them.
    gt = GT1000(transport=FakeTransport())
    order = []
    gt.close_ports = lambda: order.append("close")
    gt.open_ports = lambda: order.append("open") or True

    gt._reopen_ports()

    assert order == ["close", "open"]


def test_refresh_state_bails_once_the_scheduler_is_stopped():
    # refresh_state's cooperative cancellation no longer rides a loose self.stop
    # flag on GT1000; it is sourced from the scheduler that owns the worker.
    gt = GT1000(transport=FakeTransport())
    scanned = []
    gt.get_all_fx_type_states = lambda fx_type: scanned.append(fx_type) or []

    gt._refresh.stop()
    gt.refresh_state()

    assert scanned == []
    assert not hasattr(gt, "stop")
