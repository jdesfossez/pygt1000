"""Test bootstrap: run the suite without the compiled ``python-rtmidi`` binding.

``pygt1000.gt1000`` imports ``rtmidi`` (a C extension for real MIDI I/O) at module
load time. None of the behaviour we characterise here touches the wire, so we
install a minimal stub in ``sys.modules`` before the package is imported. Anything
that would actually send/receive MIDI is mocked at the ``GT1000`` method boundary
inside the individual tests.
"""

import sys
import types


def _install_rtmidi_stub():
    if "rtmidi" in sys.modules:
        return

    rtmidi = types.ModuleType("rtmidi")

    class _MidiPort:
        def __init__(self, *args, **kwargs):
            self._ports = []

        def get_port_count(self):
            return len(self._ports)

        def get_port_name(self, index):
            return self._ports[index]

        def get_ports(self):
            return list(self._ports)

        def open_port(self, *args, **kwargs):
            return self

        def close_port(self):
            pass

        def send_message(self, message):
            pass

        def set_callback(self, *args, **kwargs):
            pass

        def ignore_types(self, *args, **kwargs):
            pass

    rtmidi.MidiIn = _MidiPort
    rtmidi.MidiOut = _MidiPort

    midiutil = types.ModuleType("rtmidi.midiutil")

    def open_midiinput(port, *args, **kwargs):
        return _MidiPort(), port

    def open_midioutput(port, *args, **kwargs):
        return _MidiPort(), port

    midiutil.open_midiinput = open_midiinput
    midiutil.open_midioutput = open_midioutput

    rtmidi.midiutil = midiutil
    sys.modules["rtmidi"] = rtmidi
    sys.modules["rtmidi.midiutil"] = midiutil


_install_rtmidi_stub()


# Imports below run *after* the rtmidi stub is installed, on purpose: importing
# pygt1000 any earlier would pull in the real (unbuilt) binding. Hence E402.
import logging  # noqa: E402

import pytest  # noqa: E402

from pygt1000 import GT1000  # noqa: E402


@pytest.fixture
def gt():
    """A fresh GT1000 with the spec tables loaded and logging quietened."""
    logging.disable(logging.CRITICAL)
    instance = GT1000()
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
