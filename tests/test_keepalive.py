"""Characterise ``KeepAlive`` — the periodic device-liveness poll that used to
be a second hand-rolled thread (``check_alive_thread``) living loose in
``GT1000`` alongside the tested ``RefreshScheduler``.

The module owns the interval loop and its stop flag as one internal invariant.
The device poll and the "device is unresponsive" action are injected, so it
carries no wire or port knowledge and can be driven from a test without a real
device: ``check()`` runs one poll+signal cycle synchronously, the same path the
worker thread uses, so the branch logic is testable without a thread.
"""

import threading

from pygt1000.keepalive import KeepAlive


ALIVE = [0x0]


# --------------------------------------------------------------------------
# check(): one poll+signal cycle, drivable without a thread
# --------------------------------------------------------------------------

def test_check_returns_true_and_does_not_signal_when_the_poll_reads_alive():
    signalled = []
    ka = KeepAlive(
        poll=lambda: ALIVE,
        on_unresponsive=lambda: signalled.append(True),
        alive=ALIVE,
    )

    assert ka.check() is True
    assert signalled == []


def test_check_signals_and_returns_false_when_the_poll_does_not_read_alive():
    signalled = []
    ka = KeepAlive(
        poll=lambda: [0x7F],
        on_unresponsive=lambda: signalled.append(True),
        alive=ALIVE,
    )

    assert ka.check() is False
    assert signalled == [True]


def test_check_treats_a_missing_reply_as_unresponsive():
    signalled = []
    ka = KeepAlive(
        poll=lambda: None,
        on_unresponsive=lambda: signalled.append(True),
        alive=ALIVE,
    )

    assert ka.check() is False
    assert signalled == [True]


# --------------------------------------------------------------------------
# The worker thread: start() runs the poll on its interval, stop() ends it
# --------------------------------------------------------------------------

def test_started_worker_polls_on_its_interval_then_stops_cleanly():
    polled = threading.Event()
    ka = KeepAlive(
        poll=lambda: polled.set() or ALIVE,
        on_unresponsive=lambda: None,
        alive=ALIVE,
        interval_sec=0.01,
        poll_sec=0.01,
    )

    ka.start()
    try:
        assert polled.wait(timeout=2.0), "worker never polled the device"
    finally:
        ka.stop()

    assert ka.join(timeout=2.0), "worker thread did not exit after stop()"


def test_stop_before_the_first_interval_elapses_means_no_poll():
    polled = []
    ka = KeepAlive(
        poll=lambda: polled.append(True) or ALIVE,
        on_unresponsive=lambda: None,
        alive=ALIVE,
        # A long interval polled in short chunks: stop() must break the sleep
        # promptly, before the interval elapses, so the device is never polled.
        interval_sec=100.0,
        poll_sec=0.01,
    )

    ka.start()
    ka.stop()
    assert ka.join(timeout=2.0), "worker thread did not exit after stop()"
    assert polled == []


def test_join_before_start_is_a_noop():
    ka = KeepAlive(poll=lambda: ALIVE, on_unresponsive=lambda: None, alive=ALIVE)
    assert ka.join(timeout=0.1) is True
