"""Characterise the ``RefreshScheduler`` — the background refresh coordination
that used to be hand-rolled inside ``GT1000`` (``refresh_state_thread`` +
``_queue_refresh`` + ``refresh_queue`` / ``refresh_event`` / ``refresh_lock``).

The scheduler owns the queue, the wakeup event, and the lock as one internal
invariant; the actual work stays outside as injected per-type handlers, so the
scheduler carries no MIDI or device knowledge and drains a queue of tasks
against fake handlers with no wire in sight.

The old loop cleared its wakeup event *before* popping a single task, so any
extra tasks queued behind the first were stranded until the next unrelated
``submit`` happened to wake the worker again. These tests pin the fixed
invariant: one wake drains the whole queue.
"""

import threading

from pygt1000.refresh_scheduler import RefreshScheduler


def _drain(sched):
    """Synchronously drain the queue through the same path the worker uses,
    without starting a thread."""
    sched.drain()


# --------------------------------------------------------------------------
# submit + dispatch to registered handlers
# --------------------------------------------------------------------------

def test_submit_then_drain_dispatches_to_the_registered_handler():
    sched = RefreshScheduler()
    seen = []
    sched.register("full", seen.append)

    sched.submit({"type": "full"})
    _drain(sched)

    assert seen == [{"type": "full"}]


def test_each_task_type_reaches_its_own_handler():
    sched = RefreshScheduler()
    full, sliders = [], []
    sched.register("full", full.append)
    sched.register("sliders", sliders.append)

    sched.submit({"type": "full"})
    sched.submit({"type": "sliders", "fx_type": "fx", "fx_id": 1})
    _drain(sched)

    assert full == [{"type": "full"}]
    assert sliders == [{"type": "sliders", "fx_type": "fx", "fx_id": 1}]


# --------------------------------------------------------------------------
# The fixed invariant: one wake drains the whole queue, in order
# --------------------------------------------------------------------------

def test_a_single_drain_processes_every_queued_task_in_order():
    sched = RefreshScheduler()
    seen = []
    sched.register("full", seen.append)

    sched.submit({"type": "full", "n": 1})
    sched.submit({"type": "full", "n": 2})
    sched.submit({"type": "full", "n": 3})
    _drain(sched)

    # The old code popped only one per wake and stranded the rest.
    assert [t["n"] for t in seen] == [1, 2, 3]


def test_drain_leaves_the_queue_empty():
    sched = RefreshScheduler()
    sched.register("full", lambda task: None)
    sched.submit({"type": "full"})
    sched.submit({"type": "full"})

    _drain(sched)

    assert sched.pending() == []


# --------------------------------------------------------------------------
# pending() is a read-only snapshot, not the live queue
# --------------------------------------------------------------------------

def test_pending_snapshots_queued_tasks_without_exposing_the_live_queue():
    sched = RefreshScheduler()
    sched.register("full", lambda task: None)
    sched.submit({"type": "full"})

    snapshot = sched.pending()
    assert snapshot == [{"type": "full"}]
    # Mutating the snapshot must not disturb the scheduler's own queue.
    snapshot.clear()
    assert sched.pending() == [{"type": "full"}]


# --------------------------------------------------------------------------
# Unknown task types are logged, not fatal
# --------------------------------------------------------------------------

def test_unknown_task_type_is_skipped_and_does_not_block_the_queue():
    sched = RefreshScheduler()
    seen = []
    sched.register("full", seen.append)

    sched.submit({"type": "mystery"})
    sched.submit({"type": "full"})
    _drain(sched)  # must not raise

    assert seen == [{"type": "full"}]


# --------------------------------------------------------------------------
# The worker thread: start() drains submissions, stop() ends the loop
# --------------------------------------------------------------------------

def test_started_worker_drains_a_submission_then_stops_cleanly():
    sched = RefreshScheduler(poll_sec=0.01)
    done = threading.Event()
    sched.register("full", lambda task: done.set())

    sched.start()
    try:
        sched.submit({"type": "full"})
        assert done.wait(timeout=2.0), "handler never ran on the worker thread"
    finally:
        sched.stop()

    assert sched.join(timeout=2.0), "worker thread did not exit after stop()"
