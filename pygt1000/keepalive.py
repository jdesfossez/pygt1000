"""Periodic device-liveness keepalive.

``KeepAlive`` owns the "wake on an interval, poll the device, and on no reply do
something about it" loop that used to be a second hand-rolled thread
(``check_alive_thread``) living loose inside ``GT1000`` next to the tested
``RefreshScheduler`` — the exact loop + sleep + stop-flag coordination that was
extracted into modules in the first place.

The interval loop and its stop flag are one internal invariant here. The two
things that touch the device are injected, so the module carries no MIDI or port
knowledge and drains against fakes with no wire in sight:

- ``poll()`` — read the device and return whatever the liveness probe returns.
- ``on_unresponsive()`` — the action to take when the probe does not read
  ``alive`` (in production: close and reopen the ports; that policy lives in
  ``GT1000``, not here).

Interface:

- ``check()`` — run one poll+signal cycle; returns ``True`` if the device read
  ``alive``, else invokes ``on_unresponsive`` and returns ``False``. This is the
  same path the worker runs, callable directly in tests without a thread.
- ``start()`` / ``stop()`` / ``join()`` — the worker thread lifecycle. The
  interval is slept in ``poll_sec`` chunks so ``stop()`` is noticed promptly
  rather than only at the end of a full interval.
"""

import logging
import time

logger = logging.getLogger(__name__)

# The full period between liveness polls, and the chunk the interval is slept in
# so ``stop()`` bounds shutdown latency rather than an idle device holding the
# worker in ``sleep`` for a whole interval.
INTERVAL_SEC = 10.0
POLL_SEC = 1.0


class KeepAlive:
    """Owns the periodic liveness poll, its interval loop, and the stop flag."""

    def __init__(
        self,
        poll,
        on_unresponsive,
        alive,
        interval_sec=INTERVAL_SEC,
        poll_sec=POLL_SEC,
    ):
        self._poll = poll
        self._on_unresponsive = on_unresponsive
        self._alive = alive
        self._interval_sec = interval_sec
        self._poll_sec = poll_sec
        self._stopped = False
        self._thread = None

    # -- one cycle ---------------------------------------------------------

    def check(self):
        """Poll the device once; signal on no reply. Returns True if alive."""
        data = self._poll()
        if data == self._alive:
            logger.info("Device still alive")
            return True
        logger.warning("Device not responding")
        self._on_unresponsive()
        return False

    # -- worker thread -----------------------------------------------------

    def start(self):
        """Spawn the background worker that polls on the configured interval."""
        import threading

        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the worker to exit; the chunked sleep notices it promptly."""
        self._stopped = True

    def join(self, timeout=None):
        """Wait for the worker to exit; returns True once it has."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while not self._stopped:
            if self._sleep_interval():
                return
            self.check()

    def _sleep_interval(self):
        """Sleep one interval in ``poll_sec`` chunks, re-checking the stop flag
        between chunks. Returns True if a stop was seen (skip the poll)."""
        chunks = int(self._interval_sec / self._poll_sec)
        for _ in range(chunks):
            if self._stopped:
                return True
            time.sleep(self._poll_sec)
        return self._stopped
