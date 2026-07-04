"""Background refresh coordination.

``RefreshScheduler`` owns the "enqueue a refresh task, wake a worker, drain the
queue" mechanism that used to be hand-rolled inside ``GT1000`` across
``refresh_state_thread`` / ``_queue_refresh`` and the ``refresh_queue`` /
``refresh_event`` / ``refresh_lock`` trio. The queue, the wakeup event, and the
lock are one internal invariant here; the actual refresh work stays in
``GT1000`` as injected per-type handlers, so the scheduler carries no MIDI or
device knowledge and can be driven from a test without a wire.

The old loop had a latent bug: it cleared the wakeup event *before* popping a
single task from the queue, so any tasks queued behind the first were stranded
until an unrelated ``submit`` happened to set the event again (the redundant
"if the queue is now empty, clear the event" second clear never helped). Here a
single wake drains the *whole* queue, and the event is cleared under the same
lock that observes the queue empty — so a ``submit`` racing the drain either
lands before the empty-check (and is drained) or sets the event after it (and
wakes the next loop). No lost wakeups, no stranded tasks.

Interface:

- ``register(task_type, handler)`` — bind a handler to a task ``"type"``.
- ``submit(task)`` — enqueue a task dict and wake the worker.
- ``drain()`` — dispatch every queued task to its handler (used by the worker;
  callable directly in tests without a thread).
- ``pending()`` — a snapshot copy of the queued-but-not-yet-drained tasks.
- ``start()`` / ``stop()`` / ``join()`` — the worker thread lifecycle.
"""

import logging
import threading

logger = logging.getLogger(__name__)

# How long the worker blocks on the wakeup event before looping to re-check the
# stop flag. A ``submit`` sets the event and returns immediately, so this only
# bounds shutdown latency, not refresh latency.
POLL_SEC = 0.2


class RefreshScheduler:
    """Owns the refresh queue, its wakeup, and the worker thread."""

    def __init__(self, poll_sec=POLL_SEC):
        self._poll_sec = poll_sec
        self._queue = []
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._handlers = {}
        self._stopped = False
        self._thread = None

    # -- registration ------------------------------------------------------

    def register(self, task_type, handler):
        """Bind ``handler(task)`` to tasks whose ``"type"`` is ``task_type``."""
        self._handlers[task_type] = handler

    # -- enqueue -----------------------------------------------------------

    def submit(self, task):
        """Append a refresh task and wake the worker."""
        with self._lock:
            self._queue.append(task)
            self._event.set()

    def pending(self):
        """A snapshot copy of the tasks queued but not yet drained."""
        with self._lock:
            return list(self._queue)

    # -- drain -------------------------------------------------------------

    def drain(self):
        """Dispatch every queued task to its handler, in submission order.

        Clearing the event and observing the queue empty happen atomically
        under the lock, so a concurrent ``submit`` cannot be lost.
        """
        while True:
            with self._lock:
                if not self._queue:
                    self._event.clear()
                    return
                task = self._queue.pop(0)
            self._dispatch(task)

    def _dispatch(self, task):
        handler = self._handlers.get(task.get("type"))
        if handler is None:
            logger.error(f"Unknown refresh task {task}")
            return
        handler(task)

    # -- worker thread -----------------------------------------------------

    def start(self):
        """Spawn the background worker that drains submissions as they arrive."""
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the worker to exit and wake it so it notices promptly."""
        self._stopped = True
        self._event.set()

    @property
    def stopped(self):
        """Whether stop() has been signalled. A long-running handler reads this
        to cooperatively cancel between units of work on shutdown."""
        return self._stopped

    def join(self, timeout=None):
        """Wait for the worker to exit; returns True once it has."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while not self._stopped:
            if self._event.wait(self._poll_sec):
                self.drain()
