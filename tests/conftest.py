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
    """A GT1000 with a fully-populated ``current_state`` for every fx type.

    ``_process_data_from_unit`` refuses to touch state until every fx type has an
    entry (its ``len(current_state) - 1 == len(fx_types)`` guard), so tests that
    exercise the received-message pipeline need this shape in place.
    """
    gt.device_id = 0x10
    for fx_type in gt.fx_types:
        gt.current_state[fx_type] = [
            {"fx_id": "", "state": "OFF", "name": fx_type, "slider1": None, "slider2": None}
        ]
    # The fx block is addressed by id and carries a resolved effect name.
    gt.current_state["fx"] = [
        {"fx_id": "1", "state": "OFF", "name": "CHORUS", "slider1": None, "slider2": None}
    ]
    gt.current_fx_names = {1: "CHORUS"}
    return gt
