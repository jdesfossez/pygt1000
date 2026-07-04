#!/usr/bin/env python3

import time
import threading
import logging
from time import sleep
from datetime import datetime

from .constants import (
    ONE_BYTE,
    PROGRAM_CHANGE_OFFSET,
    PATCH_NAMES_LEN,
    EDITOR_REPLY2,
    EDITOR_REPLY3,
    DEVICE_ID_BCAST,
    PATCH_NAMES_BEGIN_OFFSET,
    EDITOR_MODE_ADDRESS_SET2,
    EDITOR_MODE_ADDESS_VALUE2,
    EDITOR_MODE_ADDRESS_FETCH3,
    EDITOR_MODE_ADDRESS_LEN3,
    IDENTITY_REQUEST_MSG,
)

from .chain import parse_chain, serialize_chain
from .address_map import AddressMap
from .block_reader import BlockReader
from .slider import Slider
from .patch_state import PatchState
from .refresh_scheduler import RefreshScheduler
from .sysex_codec import SysExCodec
from .device_link import RETRY_COUNT, SLEEP_WAIT_SEC, DeviceLink
from .transport import MIDI_PORT, RtMidiTransport

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
    def __init__(self, transport=None):
        self.current_state_message = None
        self.stop = False
        # The current name for fx1-4
        self.current_fx_names = {}

        # The background refresh coordination (queue + wakeup + worker) lives
        # behind RefreshScheduler. GT1000 supplies the work as per-type
        # handlers and calls submit() to enqueue:
        # {type: "full"}: scan all the blocks
        # {type: "sliders", fx_type: <fx_type>, fx_id: <fx_id>}: just the
        # sliders for a specific fx type/id.
        self._refresh = RefreshScheduler()
        self._refresh.register("full", self._refresh_full)
        self._refresh.register("sliders", self._refresh_sliders)

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
        # The address map owns the spec-table load and the encode path; GT1000
        # keeps backward-compatible accessors that delegate to it (see the
        # registry properties below).
        self._address_map = AddressMap(self.fx_types)

        # The SysEx wire format (DT1/RQ1 framing, checksum, reply parsing) lives
        # behind the codec; GT1000 supplies the negotiated device_id and calls
        # it instead of hand-rolling frames.
        self._codec = SysExCodec()

        # The device state model lives behind PatchState: it owns the state
        # dict, its lock, and last_sync_ts. GT1000 only translates decoded
        # messages and local changes into calls on it.
        self._state = PatchState(self.fx_types)

        # The read-side pipeline (address -> fetch -> decode for a block) lives
        # behind BlockReader. The device read is injected as fetch_mem so the
        # module carries no wire knowledge; the resolved fx name is read live via
        # _current_fx_name.
        self._block_reader = BlockReader(
            self._address_map, self.fetch_mem, self._current_fx_name
        )

        # Slider resolution (which two params, the eq rule, and the range +
        # value dict) lives behind Slider. The per-param value read over MIDI is
        # injected as the BlockReader's read_value so the module carries no
        # device knowledge; the resolved fx name is read live via
        # _current_fx_name.
        self._slider = Slider(
            self._address_map, self._current_fx_name, self._block_reader.read_value
        )

        # The transport is the seam to the MIDI wire. Production uses rtmidi;
        # tests inject a fake.
        self._transport = transport if transport is not None else RtMidiTransport()

        # The request/response conversation with the unit lives behind
        # DeviceLink: it owns the offset-keyed correlation, the semaphore, and
        # the wait/retry loop, installs its own inbound callback on the
        # transport, and negotiates the device id from identity replies. GT1000
        # asks it to request() reads, send() commands, and routes device-emitted
        # frames back through _process_data_from_unit.
        self._link = DeviceLink(self._transport, self._codec)
        self._link.on_unsolicited(self._process_data_from_unit)
        self._link.on_identity(self._apply_identity)

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
        """Background thread to refresh the known device state"""
        self._refresh.start()
        self.check_alive_thread = threading.Thread(target=self.check_alive_thread)
        self.check_alive_thread.start()

    def stop_refresh_thread(self):
        self.stop = True
        self._refresh.stop()

    def refresh_state(self):
        for fx_type in self.fx_types:
            logger.info(f"Refresh state for {fx_type}")
            if self.stop:
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

    def request_identity(self):
        # TODO: this should be a background thread so we update the ID if the
        # device comes online at some point
        for i in range(RETRY_COUNT):
            self._link.send(IDENTITY_REQUEST_MSG)
            sleep(SLEEP_WAIT_SEC)
            if self.device_id != DEVICE_ID_BCAST:
                logger.info(
                    f"Identity received: {self.device_id} ({hex(self.device_id)})"
                )
                return True
        logger.warning(
            f"Identity not received, using broadcast {self.device_id} ({hex(self.device_id)})"
        )
        return False

    def check_alive_thread(self):
        while not self.stop:
            for i in range(10):
                if self.stop:
                    return
                time.sleep(10 / 10)
            data = self.fetch_mem(EDITOR_MODE_ADDRESS_FETCH3, EDITOR_MODE_ADDRESS_LEN3)
            if data == EDITOR_REPLY3:
                logger.info("Device still alive")
                continue
            else:
                logger.warning("Device not responding, trying to reopen ports")
                self.close_ports()
                out = self.open_ports()
                if out is True:
                    logger.warning("Opening ports succeeded")
                else:
                    logger.warning("Opening ports failed")

    def open_ports(self, in_portname=MIDI_PORT, out_portname=MIDI_PORT):
        if not self._transport.open(in_portname, out_portname):
            return False
        return self.open_editor_mode()

    def close_ports(self):
        self._transport.close()

    def _current_fx_name(self, fx_id):
        """The resolved effect name for an fx block; injected into Slider and
        BlockReader so it follows current_fx_names even if the attribute is
        reassigned."""
        return self.current_fx_names[fx_id]

    def _get_one_fx_state(self, fx_type, fx_id, get_sliders=True):
        state = self._block_reader.read(fx_type, fx_id, "SW")
        # These don't have a TYPE field in the spec
        if fx_type in ["ns", "delay"]:
            name = f"{fx_type}{fx_id}"
        else:
            name = self._block_reader.read(fx_type, fx_id, "TYPE")
        if fx_type == "fx":
            self.current_fx_names[fx_id] = name
        if get_sliders is True:
            slider1, slider2 = self._slider.sliders_for(fx_type, fx_id, name)
            return {
                "fx_id": fx_id,
                "state": state,
                "name": name,
                "slider1": slider1,
                "slider2": slider2,
            }
        else:
            return {
                    "fx_id": fx_id,
                    "state": state,
                    "name": name,
                    }

    def get_all_fx_type_states(self, fx_type):
        logger.debug("get_all_fx_type_state")
        out = []
        for i in range(self._address_map.fx_block_count(fx_type)):
            fx_type, fx_id = self._address_map.normalize_block(fx_type, i + 1)
            out.append(self._get_one_fx_state(fx_type, fx_id))
        return out

    def get_one_fx_state(self, fx_type, fx_id, get_sliders=True):
        logger.debug("get_one_fx_state")
        for i in range(self._address_map.fx_block_count(fx_type)):
            fx_type, _fx_id = self._address_map.normalize_block(fx_type, i + 1)
            if not fx_id:
                return self._get_one_fx_state(fx_type, _fx_id, get_sliders)
            elif fx_id == _fx_id:
                return self._get_one_fx_state(fx_type, _fx_id, get_sliders)
        return None

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

    def open_editor_mode(self):
        logger.info("Opening device in editor mode")
        # Device identification
        if not self.request_identity():
            return False
        if self.model == "GT-1000CORE":
            # Special case here, the others have 4 FX blocks
            self._address_map.set_fx_block_count("fx", 3)

        # The 2 fetch operations here may break if the value returned changes at some point.
        # Not sure what is the point of those, it looks like a simple check to make sure the
        # device is responsive.

        # FIXME we don't compute the right checksum here for some reason, but the others are good
        # Disabled this fetch, it doesn't seem to respond the first time, so we timeout and retry
        # also it's not clear what that does, everything works fine without it.
        # data = self.fetch_mem(EDITOR_MODE_ADDRESS_FETCH1, EDITOR_MODE_ADDRESS_LEN1, [0])
        # if data is None:
        #    return False
        # logger.debug(f"command1 ok, received {data}")

        data = self._link.set_and_await(
            EDITOR_MODE_ADDRESS_SET2, EDITOR_MODE_ADDESS_VALUE2
        )
        if data != EDITOR_REPLY2:
            return False
        logger.debug("command2 ok")

        data = self.fetch_mem(EDITOR_MODE_ADDRESS_FETCH3, EDITOR_MODE_ADDRESS_LEN3)
        if data != EDITOR_REPLY3:
            return False
        logger.debug("command3 ok")
        logger.info("Device opened in editor mode")
        return True

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
            fx_name = self.current_fx_names[fx_id]
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
        # untouched).
        if reply.model is not None:
            self.model = reply.model

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
        # A TYPE change resolves a new effect name; refresh the cached fx name
        # and schedule a slider re-read for that block.
        if result.type_changed:
            if ret["fx_type"] == "fx":
                self.current_fx_names[int(ret["fx_id"])] = ret["str_value"]
            fx_id = int(ret["fx_id"]) if len(ret["fx_id"]) > 0 else ""
            self._refresh.submit(
                {"type": "sliders", "fx_type": ret["fx_type"], "fx_id": fx_id}
            )

    def fx_type_table_name(self, fx_type):
        return self._address_map.fx_type_table_name(fx_type)

    def get_all_fx_types(self, fx_type):
        return self._address_map.types_for(fx_type)

    def _chain_byte_list(self):
        start_section = self._address_map.start_section("efct", "0")
        option = "efct"
        setting = "CHAIN ELEMENT1"
        return self._address_map.address_for(start_section, option, setting, None)

    def read_chain(self):
        # Return the chain as a list of words
        int_chain = self.fetch_mem(self._chain_byte_list(), ONE_BYTE)
        txt_chain = []
        for i in int_chain:
            txt_chain.append(self._address_map.chain_element_name(i))
        return txt_chain

    def parse_chain(self, txt_chain):
        # Return the chain as an object list
        return parse_chain(txt_chain)

    def serialize_chain(self, chain):
        return serialize_chain(chain)

    def write_chain_from_txt(self, txt_chain):
        # Send the chain to the unit from a text list.
        # ex: ['PEDALFX', 'COMPRESSOR', 'EQUALIZER3', ...]
        int_chain = []
        for i in txt_chain:
            int_chain.append(self._address_map.chain_element_int(i))

        self._link.set(self._chain_byte_list() + int_chain)

    def write_chain_from_obj(self, obj_chain):
        txt_chain = self.serialize_chain(obj_chain)
        self.write_chain_from_txt(txt_chain)
