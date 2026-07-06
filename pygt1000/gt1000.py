#!/usr/bin/env python3

import logging
from datetime import datetime

from .constants import (
    PROGRAM_CHANGE_OFFSET,
    PATCH_NAMES_LEN,
    EDITOR_REPLY3,
    PATCH_NAMES_BEGIN_OFFSET,
    EDITOR_MODE_ADDRESS_FETCH3,
    EDITOR_MODE_ADDRESS_LEN3,
)

from .chain import parse_chain, serialize_chain, ChainCodec
from .address_map import AddressMap
from .block_reader import BlockReader
from .block_snapshot import BlockSnapshot
from .slider import Slider
from .patch_state import PatchState
from .keepalive import KeepAlive
from .refresh_scheduler import RefreshScheduler
from .sysex_codec import SysExCodec
from .device_link import DeviceLink
from .editor_session import EditorSession
from .transport import RtMidiTransport

logging.basicConfig(
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


class GT1000:
    # The constructor is the wiring point: it builds each seam and injects
    # earlier seams' methods as the device dependency of later ones. The order
    # matters (later seams are handed methods of earlier ones), and the
    # dependency directions have reasons. That whole narrative — plus what each
    # seam owns — lives in docs/architecture.md; the comments below are just
    # pointers.
    def __init__(self, transport=None):
        self.current_state_message = None

        # RefreshScheduler: background refresh coordination. GT1000 supplies the
        # work as two per-type handlers ("full" / "sliders") and calls submit().
        self._refresh = RefreshScheduler()
        self._refresh.register("full", self._refresh_full)
        self._refresh.register("sliders", self._refresh_sliders)

        # KeepAlive: periodic liveness poll. The probe is injected as a fetch;
        # the unresponsive action is EditorSession.reopen (built below).
        self._keepalive = KeepAlive(
            poll=lambda: self.fetch_mem(
                EDITOR_MODE_ADDRESS_FETCH3, EDITOR_MODE_ADDRESS_LEN3
            ),
            on_unresponsive=lambda: self._editor_session.reopen(),
            alive=EDITOR_REPLY3,
        )

        self.fx_types = [
            "comp",
            "dist",
            "preamp",
            "ns",
            "eq",
            "delay",
            "mstDelay",
            "chorus",
            "fx",
            "pedalFx",
            "reverb",
        ]
        # AddressMap: owns the spec-table load and the encode path. GT1000 keeps
        # backward-compatible accessors that delegate to it.
        self._address_map = AddressMap(self.fx_types)

        # SysExCodec: the SysEx wire format (DT1/RQ1, checksum, reply parsing).
        # Stateless; GT1000 supplies the negotiated device_id per call.
        self._codec = SysExCodec()

        # PatchState: the single owner of the known device state.
        self._state = PatchState(self.fx_types)

        # BlockReader: read one setting (address -> fetch -> decode). fetch_mem
        # is injected as the device read; fx_name is read through PatchState.
        self._block_reader = BlockReader(
            self._address_map, self.fetch_mem, self._state.fx_name
        )

        # Slider: slider policy + resolution. The per-param read is injected as
        # BlockReader.read_value (so Slider depends on BlockReader, not vice
        # versa — this is what forces the BlockSnapshot split below).
        self._slider = Slider(
            self._address_map, self._state.fx_name, self._block_reader.read_value
        )

        # BlockSnapshot: read a whole block (state + name + both sliders, the
        # ns/delay no-TYPE case, block iteration). A separate module because
        # Slider already depends on BlockReader.read_value, so this assembly
        # cannot fold into BlockReader without a cycle. See docs/architecture.md.
        self._block_snapshot = BlockSnapshot(
            self._address_map,
            self._block_reader,
            self._slider,
            self._state.set_fx_name,
        )

        # Transport: the seam to the MIDI wire. Production uses rtmidi; tests
        # inject a fake.
        self._transport = transport if transport is not None else RtMidiTransport()

        # DeviceLink: the request/response conversation on top of Transport.
        # GT1000 wires the two inbound callbacks and routes device frames back.
        self._link = DeviceLink(self._transport, self._codec)
        self._link.on_unsolicited(self._process_data_from_unit)
        self._link.on_identity(self._apply_identity)

        # EditorSession: the device bring-up sequence + reopen policy; owns the
        # Transport lifecycle. The model-specific tweak stays on the facade, in
        # _apply_identity (see docs/architecture.md).
        self._editor_session = EditorSession(self._transport, self._link)

        # ChainCodec: the effect chain's device round trip. The device
        # dependency is injected both ways — fetch_mem to read, _link.set to
        # write.
        self._chain_codec = ChainCodec(
            self._address_map, self.fetch_mem, self._link.set
        )

        logger.info(f"GT1000 instance created {self}")

    @property
    def device_id(self):
        # The negotiated device id lives on the link (it needs it to frame
        # requests and match replies); GT1000 reads/writes it through here so
        # existing callers and the codec keep seeing gt.device_id.
        return self._link.device_id

    @device_id.setter
    def device_id(self, value):
        self._link.device_id = value

    def lookup(self, address, value):
        return self._address_map.decode(address, value)

    def start_refresh_thread(self):
        """Background threads to refresh the known device state and keep the
        connection alive — one worker each, both owned by their own module."""
        self._refresh.start()
        self._keepalive.start()

    def stop_refresh_thread(self):
        self._refresh.stop()
        self._keepalive.stop()

    def refresh_state(self):
        for fx_type in self.fx_types:
            logger.info(f"Refresh state for {fx_type}")
            if self._refresh.stopped:
                return
            now = datetime.now()
            states = self.get_all_fx_type_states(fx_type)
            self._state.record_scan(fx_type, states, now)

    def get_state(self):
        return self._state.snapshot()

    # Refresh handlers the scheduler dispatches to; the scheduler carries no
    # MIDI or device knowledge, so the device work stays here.
    def _refresh_full(self, task):
        self.refresh_state()

    def _refresh_sliders(self, task):
        slider1, slider2 = self._slider.sliders_for(
            task["fx_type"], task["fx_id"], None
        )
        self._state.set_sliders(task["fx_type"], task["fx_id"], slider1, slider2)

    def open_ports(self, *args, **kwargs):
        # Thin delegate: the bring-up sequence lives on EditorSession.
        return self._editor_session.open(*args, **kwargs)

    def close_ports(self):
        # Thin delegate: the transport lifecycle lives on EditorSession.
        self._editor_session.close()

    def get_all_fx_type_states(self, fx_type):
        # Thin delegate: the whole-block assembly + iteration live on
        # BlockSnapshot.
        logger.debug("get_all_fx_type_state")
        return self._block_snapshot.snapshot_all(fx_type)

    def get_one_fx_state(self, fx_type, fx_id, get_sliders=True):
        # Thin delegate: the whole-block assembly + lookup live on BlockSnapshot.
        logger.debug("get_one_fx_state")
        return self._block_snapshot.snapshot_one(fx_type, fx_id, get_sliders)

    def fetch_mem(self, offset, length, override_checksum=None):
        # Read device memory: RQ1 out, block for the correlated reply. The
        # correlation lives in DeviceLink now.
        return self._link.request(offset, length, override_checksum)

    def fetch_patch_names(self):
        data = self.fetch_mem(PATCH_NAMES_BEGIN_OFFSET, PATCH_NAMES_LEN)
        data_offset = 0
        names = []
        # We would need to iterate over more base offsets to get the whole
        # list, unused for now but left as an example.
        for i in range(int(len(data[1]) / 16)):
            name = ""
            for j in range(16):
                name += chr(data[1][data_offset])
                data_offset += 1
            names.append(name)
        return name

    def toggle_fx_state(self, fx_type, fx_id, state):
        address_value = self._address_map.address_for_block(fx_type, fx_id, "SW", state)
        self._link.set(address_value)
        # The state model matches blocks by their normalized id (preamp A/B).
        fx_type, fx_id = self._address_map.normalize_block(fx_type, fx_id)
        self._state.set_fx(fx_type, fx_id, "state", state)

    def set_fx_value(self, fx_type, fx_id, option, value):
        # the sliders can want to send float
        value = int(value)
        if fx_type == "fx":
            fx_name = self._state.fx_name(fx_id)
            logger.info(f"Setting {fx_type}{fx_id} {fx_name} {option} to {value}")
            address_value = self._address_map.address_for_block(
                fx_type, fx_id, option, value, fx_name=fx_name
            )
        else:
            logger.info(f"Setting {fx_type}{fx_id} {option} to {value}")
            address_value = self._address_map.address_for_block(
                fx_type, fx_id, option, value
            )
        self._link.set(address_value)

    def get_fx_value_from_value_name(self, fx_type, prop, value_name):
        return self._address_map.value_for(fx_type, prop, value_name)

    def set_fx_type_type(self, fx_type, fx_id, new_type):
        type_value = self.get_fx_value_from_value_name(fx_type, "TYPE", new_type)
        if type_value is None:
            logger.error("Failed to set {fx_type}{fx_id} TYPE to {new_type}")
        address_value = self._address_map.address_for_block(
            fx_type, fx_id, "TYPE", type_value
        )
        self._link.set(address_value)

    def get_patch_names(self):
        # Fire-and-forget request for the patch-name block (the reply, if any,
        # falls through DeviceLink to _process_data_from_unit).
        self._link.send(
            self._codec.encode_rq1(
                self.device_id, PATCH_NAMES_BEGIN_OFFSET, PATCH_NAMES_LEN
            )
        )

    def _apply_identity(self, reply):
        # DeviceLink negotiated the device id and hands us the parsed reply; we
        # keep the model substitution (an unknown model leaves self.model
        # untouched) and apply the model-specific fx-block-count tweak from it.
        # This is the facade's job — EditorSession surfaces the identity but does
        # not know the block layout. It runs as the reply lands during the open
        # sequence's request_identity, before the editor-mode set, exactly where
        # the old open_editor_mode applied it.
        if reply.model is not None:
            self.model = reply.model
        if reply.model == "GT-1000CORE":
            # Special case here, the others have 4 FX blocks.
            self._address_map.set_fx_block_count("fx", 3)

    def _process_data_from_unit(self, received_offset, received_data):
        # program change, we need to refresh the whole state
        if received_offset == PROGRAM_CHANGE_OFFSET:
            logger.info(f"Switching to program {bytes_as_hex(received_data)}")
            # Unblock the refresh worker
            self._refresh.submit({"type": "full"})
            return
        ret = self.lookup(received_offset, received_data[0])
        if ret is None:
            logger.debug("unknown data received by the unit, ignoring")
            return

        result = self._state.apply(ret)
        # A TYPE change resolves a new effect name; PatchState.apply already
        # updated the resolved-fx-name owner, so we just schedule a slider
        # re-read for that block.
        if result.type_changed:
            fx_id = int(ret.fx_id) if len(ret.fx_id) > 0 else ""
            self._refresh.submit(
                {"type": "sliders", "fx_type": ret.fx_type, "fx_id": fx_id}
            )

    def fx_type_table_name(self, fx_type):
        return self._address_map.fx_type_table_name(fx_type)

    def get_all_fx_types(self, fx_type):
        return self._address_map.types_for(fx_type)

    def read_chain(self):
        # Return the chain as a list of words
        return self._chain_codec.read_names()

    def parse_chain(self, txt_chain):
        # Return the chain as an object list
        return parse_chain(txt_chain)

    def serialize_chain(self, chain):
        return serialize_chain(chain)

    def write_chain_from_txt(self, txt_chain):
        # Send the chain to the unit from a text list.
        # ex: ['PEDALFX', 'COMPRESSOR', 'EQUALIZER3', ...]
        self._chain_codec.write_names(txt_chain)

    def write_chain_from_obj(self, obj_chain):
        self._chain_codec.write(obj_chain)
