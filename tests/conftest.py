"""Test bootstrap.

``pygt1000`` no longer imports the compiled ``python-rtmidi`` binding at load
time — rtmidi is confined to ``RtMidiTransport``, which imports it lazily. Tests
build a ``GT1000`` on a ``FakeTransport`` instead: it records what the protocol
sent and can inject canned device replies, so nothing here touches the wire.
"""

import logging

import pytest

from pygt1000 import GT1000
from pygt1000.transport import FakeTransport


@pytest.fixture
def gt():
    """A fresh GT1000 on a fake transport, spec tables loaded, logging quiet."""
    logging.disable(logging.CRITICAL)
    instance = GT1000(transport=FakeTransport())
    yield instance
    logging.disable(logging.NOTSET)


@pytest.fixture
def gt_with_state(gt):
    """A GT1000 whose ``PatchState`` is fully populated for every fx type.

    ``PatchState.apply`` refuses to touch state until every fx type has an entry
    (``is_ready``), so tests that exercise the received-message pipeline need
    this shape in place. State is seeded through the module's own ``record_scan``
    rather than by reaching into the (now private) state dict.
    """
    from datetime import datetime

    gt.device_id = 0x10
    now = datetime.now()
    for fx_type in gt.fx_types:
        gt._state.record_scan(
            fx_type,
            [{"fx_id": "", "state": "OFF", "name": fx_type, "slider1": None, "slider2": None}],
            now,
        )
    # The fx block is addressed by id and carries a resolved effect name.
    gt._state.record_scan(
        "fx",
        [{"fx_id": "1", "state": "OFF", "name": "CHORUS", "slider1": None, "slider2": None}],
        now,
    )
    gt.current_fx_names = {1: "CHORUS"}
    return gt
