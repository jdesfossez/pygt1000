#!/usr/bin/env python3

import time
import threading
import logging
from time import sleep
from datetime import datetime

from .constants import (
    SYSEX_END,
    ONE_BYTE,
    PROGRAM_CHANGE_OFFSET,
    MODEL_ID,
    PATCH_NAMES_LEN,
    EDITOR_REPLY2,
    EDITOR_REPLY3,
    DEVICE_ID_BCAST,
    PATCH_NAMES_BEGIN_OFFSET,
    EDITOR_MODE_ADDRESS_SET2,
    EDITOR_MODE_ADDESS_VALUE2,
    EDITOR_MODE_ADDRESS_FETCH3,
    EDITOR_MODE_ADDRESS_LEN3,
    DT1_SYSEX_HEADER,
    DT1_COMMAND_ID,
    RQ1_SYSEX_HEADER,
    SYSEX_START,
    IDENTITY_REQUEST_MSG,
    NON_RT_MSG,
    GEN_INFO,
    IDENTITY_REPLY,
    MANUFACTURER_ID,
    FX_TO_TABLE_SUFFIX,
    GT1000_FAMILY,
)

from .chain import parse_chain, serialize_chain
from .address_map import AddressMap
from .transport import MIDI_PORT, RtMidiTransport

SLEEP_WAIT_SEC = 0.1
REFRESH_STATE_POLL_RATE_SEC = 2
RETRY_COUNT = 100

logging.basicConfig(
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


# Which two params are an effect's sliders (the policy). The mechanism —
# fetching each param's value over MIDI — lives in _get_one_slider (non-fx
# blocks) and _get_one_fx_slider (fx sub-effect tables). Either entry may be
# None, meaning "no slider"; a block/effect absent from these maps also has no
# sliders.
#
# Non-fx blocks, keyed by fx_type. ``eq`` is the one param-dependent case and
# is handled directly in _get_sliders rather than as data.
NON_FX_SLIDER_PARAMS = {
    "comp": ("SUSTAIN", "LEVEL"),
    "dist": ("DRIVE", "LEVEL"),
    "preamp": ("GAIN", "LEVEL"),
    "ns": ("THRESHOLD", "RELEASE"),
    "delay": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "mstDelay": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "chorus": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "reverb": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "pedalFx": ("EFFECT LEVEL", "DIRECT MIX"),
}

# fx block, keyed by the resolved effect name. OCTAVE BASS and TOUCH WAH BASS
# map to no sliders; any name not listed does too.
FX_NAME_SLIDER_PARAMS = {
    "AC GUITAR SIM": ("LEVEL", None),
    "AC RESONANCE": ("LEVEL", None),
    "AUTO WAH": ("EFFECT LEVEL", "DIRECT MIX"),
    "CHORUS": ("EFFECT LEVEL", "DIRECT LEVEL"),
    "CHORUS BASS": ("EFFECT LEVEL", "DEPTH"),
    "CLASSIC-VIBE": ("EFFECT LEVEL", "DEPTH"),
    "COMPRESSOR": ("LEVEL", "DIRECT MIX"),
    "DEFRETTER": ("EFFECT LEVEL", "DEPTH"),
    "DEFRETTER BASS": ("EFFECT LEVEL", "DIRECT MIX"),
    "DISTORTION": ("DRIVE", "LEVEL"),
    "FEEDBACKER": ("FEEDBACK", "OCT FEEDBACK"),
    "FLANGER": ("EFFECT LEVEL", "DIRECT MIX"),
    "FLANGER BASS": ("EFFECT LEVEL", "DIRECT MIX"),
    "HARMONIST": ("HR1:LEVEL", "DIRECT LEVEL"),
    "HUMANIZER": ("LEVEL", "DEPTH"),
    "MASTERING FX": ("TONE", "NATURAL"),
    "OCTAVE": ("OCTAVE LEVEL", "DIRECT LEVEL"),
    "OCTAVE BASS": (None, None),
    "OVERTONE": ("UPPER LEVEL", "DIRECT LEVEL"),
    "PAN": ("EFFECT LEVEL", "DIRECT MIX"),
    "PHASER": ("EFFECT LEVEL", "DIRECT MIX"),
    "PITCH SHIFTER": ("PS1:LEVEL", "DIRECT LEVEL"),
    "RING MOD": ("EFFECT LEVEL", "DIRECT MIX"),
    "ROTARY": ("EFFECT LEVEL", "DIRECT MIX"),
    "S-BEND": ("FALL TIME", "RISE TIME"),
    "SITAR SIM": ("EFFECT LEVEL", "DIRECT MIX"),
    "SLICER": ("EFFECT LEVEL", "DIRECT MIX"),
    "SLOW GEAR": ("LEVEL", "SENS"),
    "SLOW GEAR BASS": ("LEVEL", "SENS"),
    "SOUND HOLD": ("EFFECT LEVEL", "RISE TIME"),
    "TOUCH WAH": ("EFFECT LEVEL", "DIRECT MIX"),
    "TOUCH WAH BASS": (None, None),
    "TREMOLO": ("EFFECT LEVEL", "DIRECT MIX"),
    "VIBRATO": ("EFFECT LEVEL", "DIRECT MIX"),
}


class GT1000:
    def __init__(self, transport=None):
        self.device_id = DEVICE_ID_BCAST
        self.current_state_message = None
        self.received_data = {}
        self.data_semaphore = threading.Semaphore(1)
        self.stop = False
        # The current name for fx1-4
        self.current_fx_names = {}

        # Block the refresh thread until we detect a program change
        self.refresh_event = threading.Event()
        # What do we need to refresh when the refresh thread kicks off ?
        # {type: "full"}: scan all the blocks
        # {type: "sliders", fx_type: <fx_type>, fx_id: <fx_id>}: just the sliders for
        # a specific fx type/id.
        # Add/remove to the queue protected by the state_lock
        self.refresh_queue = []

        # Protect current_state and prevent state changes while refreshing
        self.state_lock = threading.Semaphore(1)
        # The known state of the effects
        self.current_state = {"last_sync_ts": {}}

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

        # The transport is the seam to the MIDI wire. Production uses rtmidi;
        # tests inject a fake. Inbound raw messages are delivered back to the
        # protocol via process_received_message; the request/response
        # correlation (received_data + wait/retry) lives here on the protocol
        # side, driven by that callback.
        self._transport = transport if transport is not None else RtMidiTransport()
        self._transport.set_on_receive(self.process_received_message)

        logger.info(f"GT1000 instance created {self}")

    # -- Address-map registries (delegated) ---------------------------------
    # These six registries now live on the AddressMap module; the properties
    # preserve the historical ``self.<registry>`` read access used across the
    # class and the characterisation tests.
    @property
    def tables(self):
        return self._address_map.tables

    @property
    def first_two_bytes(self):
        return self._address_map.first_two_bytes

    @property
    def offset_in_patch_tables(self):
        return self._address_map.offset_in_patch_tables

    @property
    def last_byte_option(self):
        return self._address_map.last_byte_option

    @property
    def fx_tables(self):
        return self._address_map.fx_tables

    @property
    def fx_types_count(self):
        return self._address_map.fx_types_count

    def lookup(self, address, value):
        return self._address_map.decode(address, value)

    def start_refresh_thread(self):
        """Background thread to refresh the known device state"""
        self.refresh_thread = threading.Thread(target=self.refresh_state_thread)
        self.check_alive_thread = threading.Thread(target=self.check_alive_thread)
        self.refresh_thread.start()
        self.check_alive_thread.start()

    def stop_refresh_thread(self):
        self.stop = True

    def refresh_state(self):
        for fx_type in self.fx_types:
            logger.info(f"Refresh state for {fx_type}")
            if self.stop:
                return
            now = datetime.now()
            current_state = self.get_all_fx_type_states(fx_type)
            with self.state_lock:
                self.current_state[fx_type] = current_state
                self.current_state["last_sync_ts"][fx_type] = now

    def get_state(self):
        with self.state_lock:
            return self.current_state

    def refresh_state_thread(self):
        while not self.stop:
            time.sleep(REFRESH_STATE_POLL_RATE_SEC / 10)
            if self.refresh_event.is_set():
                self.refresh_event.clear()
                with self.state_lock:
                    if len(self.refresh_queue) < 1:
                        logger.error("Refresh started, but refresh queue empty")
                        continue
                    task = self.refresh_queue.pop(0)
                    # If it was the last event, clear the refresh_event
                    if len(self.refresh_queue) == 0:
                        self.refresh_event.clear()
                if task["type"] == "full":
                    self.refresh_state()
                elif task["type"] == "sliders":
                    slider1, slider2 = self._get_sliders(
                        task["fx_type"], task["fx_id"], None
                    )
                    with self.state_lock:
                        for fx in self.current_state[task["fx_type"]]:
                            if str(fx["fx_id"]) == str(task["fx_id"]):
                                fx["slider1"] = slider1
                                fx["slider2"] = slider2
                                break
                else:
                    logger.error("Unknown refresh task {task}")

    def request_identity(self):
        # TODO: this should be a background thread so we update the ID if the
        # device comes online at some point
        for i in range(RETRY_COUNT):
            self.send_message(IDENTITY_REQUEST_MSG)
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

    def _get_one_fx_type_value(self, fx_type, fx_id, value_entry, just_range=False):
        offset = self._construct_address_value(
            self._get_start_section(fx_type, str(fx_id)),
            f"{fx_type}{fx_id}",
            value_entry,
            None,
        )
        data = self.fetch_mem(offset, ONE_BYTE)
        if data is None:
            logger.warning(f"_get_one_fx_state no data for {fx_type}{fx_id}")
            return None
        fx_table = self.tables[self.fx_tables[fx_type]]
        # If we just want the numerical value_range, not the text mapping
        if just_range is True:
            return data[0]
        for i in fx_table[value_entry]["values"]:
            if data[0] == fx_table[value_entry]["values"][i]:
                return i
        # If there is not text mapping to the value, just return the value
        return data[0]

    def _get_one_fx_value(self, fx_type, fx_id, value_entry):
        fx_name = self.current_fx_names[fx_id]
        logger.info(f"FX_VALUE for {fx_name} , {fx_type}{fx_id}, {value_entry}")
        offset = self._construct_address_value(
            self._get_fx_start_section(fx_id, fx_name),
            f"{fx_type}{fx_id}{FX_TO_TABLE_SUFFIX[fx_name]}",
            value_entry,
            None,
        )
        if offset is None:
            return None
        data = self.fetch_mem(offset, ONE_BYTE)
        if data is None:
            logger.warning(f"_get_one_fx_value no data for {fx_type}{fx_id} {fx_name}")
            return None
        fx_table = self.tables[f"PatchFx{FX_TO_TABLE_SUFFIX[fx_name]}"]
        for i in fx_table[value_entry]["values"]:
            if data[0] == fx_table[value_entry]["values"][i]:
                return i
        # If there is no text mapping to the value, just return the value
        return data[0]

    def _get_one_slider(self, fx_type, fx_id, option):
        value_range = self._lookup_value_range(
            self._get_start_section(fx_type, str(fx_id)), f"{fx_type}{fx_id}", option
        )
        value = self._get_one_fx_type_value(fx_type, fx_id, option, just_range=True)
        return {
            "value": value,
            "label": option,
            "min": value_range[0],
            "max": value_range[1],
        }

    def _get_one_fx_slider(self, fx_type, fx_id, option):
        fx_name = self.current_fx_names[fx_id]
        value_range = self._lookup_value_range(
            self._get_fx_start_section(fx_id, fx_name),
            f"{fx_type}{fx_id}{FX_TO_TABLE_SUFFIX[fx_name]}",
            option,
        )
        value = self._get_one_fx_value(fx_type, fx_id, option)
        if value is None:
            return None
        return {
            "value": value,
            "label": option,
            "min": value_range[0],
            "max": value_range[1],
        }

    @staticmethod
    def _fetch_slider(fetch, fx_type, fx_id, param):
        """Fetch one slider via ``fetch``, or None when the param is None."""
        if param is None:
            return None
        return fetch(fx_type, fx_id, param)

    def _get_sliders(self, fx_type, fx_id, param_name):
        # eq is the one param-dependent block; keep it out of the data table.
        if fx_type == "eq":
            param1 = "LEVEL1" if param_name == "PARAMETRIC" else "LEVEL"
            return self._get_one_slider(fx_type, fx_id, param1), None

        if fx_type == "fx":
            fetch = self._get_one_fx_slider
            param1, param2 = FX_NAME_SLIDER_PARAMS.get(
                self.current_fx_names[fx_id], (None, None)
            )
        else:
            fetch = self._get_one_slider
            param1, param2 = NON_FX_SLIDER_PARAMS.get(fx_type, (None, None))

        return (
            self._fetch_slider(fetch, fx_type, fx_id, param1),
            self._fetch_slider(fetch, fx_type, fx_id, param2),
        )

    def _get_one_fx_state(self, fx_type, fx_id, get_sliders=True):
        state = self._get_one_fx_type_value(fx_type, fx_id, "SW")
        # These don't have a TYPE field in the spec
        if fx_type in ["ns", "delay"]:
            name = f"{fx_type}{fx_id}"
        else:
            name = self._get_one_fx_type_value(fx_type, fx_id, "TYPE")
        if fx_type == "fx":
            self.current_fx_names[fx_id] = name
        if get_sliders is True:
            slider1, slider2 = self._get_sliders(fx_type, fx_id, name)
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
        for i in range(self.fx_types_count[fx_type]):
            fx_type, fx_id = self._normalize_fx_block(fx_type, i + 1)
            out.append(self._get_one_fx_state(fx_type, fx_id))
        return out

    def get_one_fx_state(self, fx_type, fx_id, get_sliders=True):
        logger.debug("get_one_fx_state")
        for i in range(self.fx_types_count[fx_type]):
            fx_type, _fx_id = self._normalize_fx_block(fx_type, i + 1)
            if not fx_id:
                return self._get_one_fx_state(fx_type, _fx_id, get_sliders)
            elif fx_id == _fx_id:
                return self._get_one_fx_state(fx_type, _fx_id, get_sliders)
        return None

    def fetch_mem(self, offset, length, override_checksum=None):
        self.send_message(
            self.assemble_message(RQ1_SYSEX_HEADER, offset + length, override_checksum),
            offset=offset,
        )
        data = self.wait_recv_data(offset)
        if data is not None:
            with self.data_semaphore:
                del self.received_data[str(offset)]
        return data

    def set_byte(self, offset, data):
        self.send_message(
            self.assemble_message(DT1_SYSEX_HEADER, offset + data), offset
        )

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
            self.fx_types_count["fx"] = 3

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

        self.set_byte(EDITOR_MODE_ADDRESS_SET2, EDITOR_MODE_ADDESS_VALUE2)
        data = self.wait_recv_data(EDITOR_MODE_ADDRESS_SET2)
        if data != EDITOR_REPLY2:
            return False
        logger.debug("command2 ok")

        data = self.fetch_mem(EDITOR_MODE_ADDRESS_FETCH3, EDITOR_MODE_ADDRESS_LEN3)
        if data != EDITOR_REPLY3:
            return False
        logger.debug("command3 ok")
        logger.info("Device opened in editor mode")
        return True

    def calculate_checksum(self, data):
        total = sum(data) % 128
        return [128 - total]

    def _get_start_section(self, fx_type, fx_id):
        return self._address_map.start_section(fx_type, fx_id)

    def _get_fx_start_section(self, fx_id, fx_name):
        return self._address_map.fx_start_section(fx_id, FX_TO_TABLE_SUFFIX[fx_name])

    def _normalize_fx_block(self, fx_type, fx_id):
        if self.fx_types_count[fx_type] == 1:
            fx_id = ""
        elif fx_type == "preamp" and fx_id == 1:
            fx_id = "A"
        elif fx_type == "preamp" and fx_id == 2:
            fx_id = "B"
        return fx_type, fx_id

    def toggle_fx_state(self, fx_type, fx_id, state):
        fx_type, fx_id = self._normalize_fx_block(fx_type, fx_id)
        # Strip the number for blocks with only one instance
        with self.state_lock:
            self.send_message(
                self.build_dt_message(
                    self._get_start_section(fx_type, fx_id),
                    f"{fx_type}{fx_id}",
                    "SW",
                    state,
                )
            )
            if len(self.current_state[fx_type]) == 1:
                self.current_state[fx_type][0]["state"] = state
            else:
                for i in self.current_state[fx_type]:
                    if str(i["fx_id"]) == str(fx_id):
                        i["state"] = state

    def set_fx_value(self, fx_type, fx_id, option, value):
        # the sliders can want to send float
        value = int(value)
        # Strip the number for blocks with only one instance
        fx_type, fx_id = self._normalize_fx_block(fx_type, fx_id)
        if fx_type == "fx":
            fx_name = self.current_fx_names[fx_id]
            table_suffix = FX_TO_TABLE_SUFFIX[fx_name]
            full_name = f"fx{fx_id}{table_suffix}"
            logger.info(
                f"Setting {fx_type}{fx_id} {fx_name} ({full_name}) {option} to {value}"
            )
            self.send_message(
                self.build_dt_message(
                    self._get_fx_start_section(fx_id, fx_name),
                    full_name,
                    option,
                    value,
                )
            )
        else:
            logger.info(f"Setting {fx_type}{fx_id} {option} to {value}")
            self.send_message(
                self.build_dt_message(
                    self._get_start_section(fx_type, fx_id),
                    f"{fx_type}{fx_id}",
                    option,
                    value,
                )
            )

    def get_fx_value_from_value_name(self, fx_type, prop, value_name):
        return self._address_map.value_for(fx_type, prop, value_name)

    def set_fx_type_type(self, fx_type, fx_id, new_type):
        type_value = self.get_fx_value_from_value_name(fx_type, "TYPE", new_type)
        if type_value is None:
            logger.error("Failed to set {fx_type}{fx_id} TYPE to {new_type}")
        fx_type, fx_id = self._normalize_fx_block(fx_type, fx_id)
        self.send_message(
            self.build_dt_message(
                self._get_start_section(fx_type, fx_id),
                f"{fx_type}{fx_id}",
                "TYPE",
                type_value,
            )
        )

    def send_message(self, message, offset=None):
        with self.data_semaphore:
            self.received_data[str(offset)] = None
            logger.debug(f"sending: {bytes_as_hex(message)}")
            self._transport.send(message)

    def _build_message(self, header, address_value, override_checksum=None):
        if override_checksum is not None:
            checksum = override_checksum
        else:
            checksum = self.calculate_checksum(address_value)
        # Substitute our negotiated device id for the broadcast address without
        # mutating the shared module-level header constant.
        header = header[:1] + [self.device_id] + header[2:]
        return SYSEX_START + header + address_value + checksum + SYSEX_END

    def build_dt_message(self, start_section, option, setting, param):
        address_value = self._construct_address_value(
            start_section, option, setting, param
        )
        return self._build_message(DT1_SYSEX_HEADER, address_value)

    def build_rq_message(self, start_address, length):
        address_value = start_address + length
        return self._build_message(RQ1_SYSEX_HEADER, address_value)

    def assemble_message(self, header, payload, override_checksum=None):
        return self._build_message(header, payload, override_checksum)

    def get_patch_names(self):
        self.send_message(
            self.build_rq_message(PATCH_NAMES_BEGIN_OFFSET, PATCH_NAMES_LEN)
        )

    def wait_recv_data(self, offset=None):
        for i in range(RETRY_COUNT):
            with self.data_semaphore:
                if self.received_data[str(offset)] is not None:
                    return self.received_data[str(offset)]
            sleep(SLEEP_WAIT_SEC)
        return None

    def _msg_identity_reply(self, message):
        # Byte Explanation
        # F0H: System Exclusive Message status
        # 7EH: ID Number (Universal Non-realtime Message)
        # dev: Device ID (dev: 00H - 1FH)
        # 06H: Sub ID # 1 (General Information)
        # 02H: Sub ID # 2 (Identity Reply)
        # 41H: Roland's manufacturer ID
        # 4FH,03H: Device family code (GT-1000/GT-1000CORE)
        # 00H,00H: Device family number code LSB, MSB
        # nnH: Software revision level # 1 (GT-1000:00H,GT-1000L:01H,GT-1000CORE:02H)
        # 00H: Software revision level # 2
        # vvH: Software revision level # 3 (GT-1000:01H,GT-1000L:01H,GT-1000CORE:00H)
        # 00H: Software revision level # 4
        # F7H: EOX (End of Exclusive)
        # 0xf0, 0x7e, 0x10, 0x6, 0x2, 0x41, 0x4f, 0x3, 0x0, 0x0, 0x2, 0x0, 0x0, 0x0, 0xf7
        if len(message) != 15:
            return False
        if (
            message[0] == SYSEX_START[0]
            and message[1] == NON_RT_MSG[0]
            # message[2] is the identity
            and message[3] == GEN_INFO[0]
            and message[4] == IDENTITY_REPLY[0]
            and message[5] == MANUFACTURER_ID[0]
            and message[6] == GT1000_FAMILY[0]
            and message[7] == GT1000_FAMILY[1]
        ):
            device_id = message[2]
            software_rev_1 = message[10]
            software_rev_2 = message[12]
        else:
            return False
        if software_rev_1 == 0x00 and software_rev_2 == 0x01:
            logger.info("GT-1000 detected")
            self.model = "GT-1000"
        elif software_rev_1 == 0x01 and software_rev_2 == 0x01:
            logger.info("GT-1000L detected")
            self.model = "GT-1000L"
        elif software_rev_1 == 0x02 and software_rev_2 == 0x00:
            logger.info("GT-1000CORE detected")
            self.model = "GT-1000CORE"
        else:
            logger.warning(
                f"Unknown model detected: [{hex(software_rev_1)}, {hex(software_rev_2)}]"
            )
        self.device_id = device_id
        return True

    def process_received_message(self, message):
        # Process the data received by the callback
        try:
            # logger.debug("receiving")
            if self.device_id == DEVICE_ID_BCAST and self._msg_identity_reply(message):
                logger.debug("identity ok")
                return
            received_data_header = (
                SYSEX_START
                + MANUFACTURER_ID
                + [self.device_id]
                + MODEL_ID
                + DT1_COMMAND_ID
            )
            # Make sure this message is coming from the unit
            for i in range(len(received_data_header)):
                if message[i] != received_data_header[i]:
                    # logger.debug("Ignored received data")
                    return
            received_offset = message[
                len(received_data_header) : len(received_data_header) + 4
            ]
            # The actual data is after the header and before the checksum + SYSEX_END
            received_data = message[len(received_data_header) + 4 : -2]
            logger.debug(
                f"data received: {bytes_as_hex(received_data)} for offset {bytes_as_hex(received_offset)}"
            )
            with self.data_semaphore:
                # If we are expecting that data, save it and return
                if str(received_offset) in self.received_data:
                    logger.debug("returning data")
                    self.received_data[str(received_offset)] = received_data
                    return
            # If we are not waiting for that data, it's a message sent by the unit
            # on its own we need to decode it and process the state change
            logger.debug("data emitted by the unit")
            self._process_data_from_unit(received_offset, received_data)
        # Catch-all because otherwise nothing gets logged from the callback
        # context and it is very confusing
        except Exception:
            logger.exception("process_received_message")

    def _process_data_from_unit(self, received_offset, received_data):
        # program change, we need to refresh the whole state
        if received_offset == PROGRAM_CHANGE_OFFSET:
            logger.info(f"Switching to program {bytes_as_hex(received_data)}")
            # Unblock the refresh thread
            with self.state_lock:
                self.refresh_queue.append({"type": "full"})
            self.refresh_event.set()
            return
        ret = self.lookup(received_offset, received_data[0])
        if ret is None:
            logger.debug("unknown data received by the unit, ignoring")
            return

        now = datetime.now()
        with self.state_lock:
            # Make sure we finished the first state gathering before entering
            # here.
            if (len(self.current_state) - 1) != len(self.fx_types):
                return
            for fx in self.current_state[ret["fx_type"]]:
                if str(fx["fx_id"]) != str(ret["fx_id"]):
                    continue
                matches = False
                if ret["value_name"] == "SW":
                    logger.info(
                        f"{ret['fx_type']}{ret['fx_id']}: {fx['state']} -> {ret['str_value']}"
                    )
                    fx["state"] = ret["str_value"]
                    matches = True
                elif ret["value_name"] == "TYPE":
                    logger.info(
                        f"{ret['fx_type']}{ret['fx_id']}: {fx['name']} -> {ret['str_value']}"
                    )
                    fx["name"] = ret["str_value"]
                    if ret["fx_type"] == "fx":
                        self.current_fx_names[int(ret["fx_id"])] = fx["name"]
                    if len(ret["fx_id"]) > 0:
                        fx_id = int(ret["fx_id"])
                    else:
                        fx_id = ""
                    self.refresh_queue.append(
                        {"type": "sliders", "fx_type": ret["fx_type"], "fx_id": fx_id}
                    )
                    self.refresh_event.set()
                    matches = True
                else:
                    if fx["slider1"] is not None:
                        if fx["slider1"]["label"] == ret["value_name"]:
                            logger.info(
                                f"{ret['fx_type']}{ret['fx_id']} slider1 {ret['value_name']}: {fx['slider1']['value']} -> {ret['int_value']}"
                            )
                            fx["slider1"]["value"] = ret["int_value"]
                            matches = True
                    if fx["slider2"] is not None:
                        if fx["slider2"]["label"] == ret["value_name"]:
                            logger.info(
                                f"{ret['fx_type']}{ret['fx_id']} slider2 {ret['value_name']}: {fx['slider2']['value']} -> {ret['int_value']}"
                            )
                            fx["slider2"]["value"] = ret["int_value"]
                            matches = True
                if matches:
                    self.current_state["last_sync_ts"][ret["fx_type"]] = now
                return

    def _construct_address_value(self, start_section, option, setting, param):
        # param is the value we want to set, if None we just construct the base address
        return self._address_map.address_for(start_section, option, setting, param)

    def _lookup_value_range(self, start_section, option, setting):
        return self._address_map.value_range(start_section, option, setting)

    def fx_type_table_name(self, fx_type):
        return self._address_map.fx_type_table_name(fx_type)

    def get_all_fx_types(self, fx_type):
        return self._address_map.types_for(fx_type)

    def _chain_byte_list(self):
        start_section = self._get_start_section("efct", "0")
        option = "efct"
        setting = "CHAIN ELEMENT1"
        return self._construct_address_value(start_section, option, setting, None)

    def read_chain(self):
        # Return the chain as a list of words
        int_chain = self.fetch_mem(self._chain_byte_list(), ONE_BYTE)
        txt_chain = []
        for i in int_chain:
            txt_chain.append(self.tables["ChainElement"][str(i)])
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
            int_chain.append(int(self.tables["ChainElement"][i]))

        set_chain = self._build_message(
            DT1_SYSEX_HEADER, self._chain_byte_list() + int_chain
        )
        self.send_message(set_chain)

    def write_chain_from_obj(self, obj_chain):
        txt_chain = self.serialize_chain(obj_chain)
        self.write_chain_from_txt(txt_chain)
