#!/usr/bin/env python3

import json
import rtmidi
import time
import threading
import logging
from time import sleep
from datetime import datetime
from rtmidi.midiutil import open_midiinput, open_midioutput
from pathlib import Path

from .constants import (
    SYSEX_END,
    ONE_BYTE,
    MODEL_ID,
    PATCH_NAMES_LEN,
    EDITOR_REPLY1,
    EDITOR_REPLY2,
    EDITOR_REPLY3,
    DEVICE_ID_BCAST,
    PATCH_NAMES_BEGIN_OFFSET,
    EDITOR_MODE_ADDRESS_FETCH1,
    EDITOR_MODE_ADDRESS_LEN1,
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

MIDI_PORT = "GT-1000:GT-1000 MIDI 1"
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


def bytes_to_int(value):
    return int.from_bytes(value, byteorder="big")


def bytes_as_hex(data):
    return "[{}]".format(", ".join(hex(x) for x in data))


class MidiInputHandler(object):
    def __init__(self, port):
        self.port = port
        self._wallclock = time.time()

    def __call__(self, event, gt1000):
        message, deltatime = event
        self._wallclock += deltatime
        logger.debug(
            "[%s] @%0.6f %s" % (self.port, self._wallclock, bytes_as_hex(message))
        )
        gt1000.process_received_message(message)


class GT1000:
    def __init__(self):
        self.tables = {}
        self.device_id = DEVICE_ID_BCAST
        self.current_state_message = None
        self.received_data = {}
        self.data_semaphore = threading.Semaphore(1)
        self.stop = False
        # The current name for fx1-4
        self.current_fx_names = {}

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
        ]
        self.fx_tables = {}
        self.fx_types_count = {}
        self._import_specs_tables()

        logger.info(f"GT1000 instance created {self}")

    def _import_specs_tables(self):
        for i in self.fx_types:
            self.fx_types_count[i] = 0
        for i in (Path(__file__).parent / "specs").glob("*.json"):
            table_name = i.name.split(".")[0]
            table = json.loads(i.read_text())
            if table_name == "Patch" or table_name == "Patch3":
                for key in table:
                    if key in ["preampA", "preampB"]:
                        fx_type = "preamp"
                    else:
                        fx_type = "".join(i for i in key if not i.isdigit())
                    if fx_type not in self.fx_types:
                        continue
                    self.fx_types_count[fx_type] += 1
                    self.fx_tables[fx_type] = table[key]["table"]
            self.tables[table_name] = table

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
            for i in range(10):
                if self.stop:
                    return
                time.sleep(REFRESH_STATE_POLL_RATE_SEC / 10)
            self.refresh_state()

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
                logger.warning(
                    "Device not responding, trying to open {self.portname} again"
                )
                self.close_ports()
                out = self.open_ports(self.portname)
                if out is True:
                    logger.warning("Opening ports succeeded")
                else:
                    logger.warning("Opening ports failed")

    def _get_midi_exact_port_names(self, in_portname, out_portname):
        """The portname usually contains an ID that can change depending on the other devices"""
        tmp_midi_out = rtmidi.MidiOut()
        port_count = tmp_midi_out.get_port_count()
        out_portname = None
        for i in range(port_count):
            if tmp_midi_out.get_port_name(i).startswith(out_portname):
                out_portname = tmp_midi_out.get_port_name(i)

        tmp_midi_in = rtmidi.MidiIn()
        port_count = tmp_midi_in.get_port_count()
        in_portname = None
        for i in range(port_count):
            if tmp_midi_in.get_port_name(i).startswith(in_portname):
                in_portname = tmp_midi_in.get_port_name(i)
        if in_portname is None:
            logger.error(
                f"Failed to find MIDI input port. Found {tmp_midi_in.get_ports()}"
            )

        if out_portname is None:
            logger.error(
                f"Failed to find MIDI output port. Found {tmp_midi_out.get_ports()}"
            )
        return in_portname, out_portname

    def open_ports(self, in_portname=MIDI_PORT, out_portname=MIDI_PORT):
        self.in_portname = in_portname
        self.out_portname = out_portname
        in_portname, out_portname = self._get_midi_exact_port_names(
            in_portname, out_portname
        )
        if in_portname is None or out_portname is None:
            return False
        try:
            self.midi_out, port_name = open_midioutput(out_portname)
        except (EOFError, KeyboardInterrupt):
            return False

        try:
            self.midi_in, port_name = open_midiinput(in_portname)
        except (EOFError, KeyboardInterrupt):
            return False
        self.midi_in.ignore_types(sysex=False)
        self.midi_in.set_callback(MidiInputHandler(in_portname), self)
        return self.open_editor_mode()

    def close_ports(self):
        self.midi_out.close_port()
        self.midi_in.close_port()

    def _get_one_fx_type_value(self, fx_type, fx_id, value_entry, just_range=False):
        offset = self._construct_address_value(
            self._get_start_section(fx_type, fx_id),
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
            self._get_start_section(fx_type, fx_id), f"{fx_type}{fx_id}", option
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
        return {
            "value": value,
            "label": option,
            "min": value_range[0],
            "max": value_range[1],
        }

    def _get_sliders(self, fx_type, fx_id, param_name):
        if fx_type == "comp":
            slider1 = self._get_one_slider(fx_type, fx_id, "SUSTAIN")
            slider2 = self._get_one_slider(fx_type, fx_id, "LEVEL")
        elif fx_type == "dist":
            slider1 = self._get_one_slider(fx_type, fx_id, "DRIVE")
            slider2 = self._get_one_slider(fx_type, fx_id, "LEVEL")
        elif fx_type == "preamp":
            slider1 = self._get_one_slider(fx_type, fx_id, "GAIN")
            slider2 = self._get_one_slider(fx_type, fx_id, "LEVEL")
        elif fx_type == "ns":
            slider1 = self._get_one_slider(fx_type, fx_id, "THRESHOLD")
            slider2 = self._get_one_slider(fx_type, fx_id, "RELEASE")
        elif fx_type == "eq":
            if param_name == "PARAMETRIC":
                slider1 = self._get_one_slider(fx_type, fx_id, "LEVEL1")
            else:
                slider1 = self._get_one_slider(fx_type, fx_id, "LEVEL")
            slider2 = None
        elif fx_type in ["delay", "mstDelay", "chorus", "reverb"]:
            slider1 = self._get_one_slider(fx_type, fx_id, "EFFECT LEVEL")
            slider2 = self._get_one_slider(fx_type, fx_id, "DIRECT LEVEL")
        elif fx_type == "pedalFx":
            slider1 = self._get_one_slider(fx_type, fx_id, "EFFECT LEVEL")
            slider2 = self._get_one_slider(fx_type, fx_id, "DIRECT MIX")
        elif fx_type == "fx":
            if self.current_fx_names[fx_id] in ["AC GUITAR SIM", "AC RESONANCE"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "LEVEL")
                slider2 = None
            elif self.current_fx_names[fx_id] in [
                "AUTO WAH",
                "DEFRETTER BASS",
                "FLANGER",
                "PAN",
                "PHASER",
                "RING MOD",
                "ROTARY",
                "SITAR SIM",
                "SLICER",
                "TOUCH WAH",
                "TREMOLO",
                "VIBRATO",
                "FLANGER BASS",
            ]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "EFFECT LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT MIX")
            elif self.current_fx_names[fx_id] in ["CHORUS"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "EFFECT LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT LEVEL")
            elif self.current_fx_names[fx_id] in ["OVERTONE"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "UPPER LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT LEVEL")
            elif self.current_fx_names[fx_id] in ["OCTAVE"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "OCTAVE LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT LEVEL")
            elif self.current_fx_names[fx_id] in [
                "CLASSIC-VIBE",
                "DEFRETTER",
                "CHORUS BASS",
            ]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "EFFECT LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DEPTH")
            elif self.current_fx_names[fx_id] in ["SOUND HOLD"]:
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "RISE TIME")
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "EFFECT LEVEL")
            elif self.current_fx_names[fx_id] in ["S-BEND"]:
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "RISE TIME")
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "FALL TIME")
            elif self.current_fx_names[fx_id] in ["HUMANIZER"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DEPTH")
            elif self.current_fx_names[fx_id] in ["DISTORTION"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "DRIVE")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "LEVEL")
            elif self.current_fx_names[fx_id] in ["MASTERING FX"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "TONE")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "NATURAL")
            elif self.current_fx_names[fx_id] in ["SLOW GEAR", "SLOW GEAR BASS"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "SENS")
            elif self.current_fx_names[fx_id] in ["COMPRESSOR"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT MIX")
            elif self.current_fx_names[fx_id] in ["FEEDBACKER"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "FEEDBACK")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "OCT FEEDBACK")
            elif self.current_fx_names[fx_id] in ["HARMONIST"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "HR1:LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT LEVEL")
            elif self.current_fx_names[fx_id] in ["PITCH SHIFTER"]:
                slider1 = self._get_one_fx_slider(fx_type, fx_id, "PS1:LEVEL")
                slider2 = self._get_one_fx_slider(fx_type, fx_id, "DIRECT LEVEL")
            else:
                slider1 = None
                slider2 = None
        else:
            slider1 = None
            slider2 = None
        return slider1, slider2

    def _get_one_fx_state(self, fx_type, fx_id):
        state = self._get_one_fx_type_value(fx_type, fx_id, "SW")
        # These don't have a TYPE field in the spec
        if fx_type in ["ns", "delay"]:
            name = f"{fx_type}{fx_id}"
        else:
            name = self._get_one_fx_type_value(fx_type, fx_id, "TYPE")
        if fx_type == "fx":
            self.current_fx_names[fx_id] = name
        slider1, slider2 = self._get_sliders(fx_type, fx_id, name)
        return {
            "fx_id": fx_id,
            "state": state,
            "name": name,
            "slider1": slider1,
            "slider2": slider2,
        }

    def get_all_fx_type_states(self, fx_type):
        logger.debug("get_all_fx_type_state")
        out = []
        for i in range(self.fx_types_count[fx_type]):
            # Blocks with a single instance like "comp" don't have an numeric ID
            if self.fx_types_count[fx_type] == 1:
                fx_id = ""
            elif fx_type == "preamp" and i == 0:
                fx_id = "A"
            elif fx_type == "preamp" and i == 1:
                fx_id = "B"
            else:
                fx_id = str(i + 1)
            out.append(self._get_one_fx_state(fx_type, fx_id))
        return out

    def fetch_mem(self, offset, length, override_checksum=None):
        self.send_message(
            self.assemble_message(RQ1_SYSEX_HEADER, offset + length, override_checksum),
            offset=offset,
        )
        return self.wait_recv_data(offset)

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
        data = self.fetch_mem(EDITOR_MODE_ADDRESS_FETCH1, EDITOR_MODE_ADDRESS_LEN1, [0])
        if data != EDITOR_REPLY1:
            return False
        logger.debug("command1 ok")

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
        if not isinstance(fx_id, str):
            logger.error("fx_id should be a string")
            fx_id = str(fx_id)
        if fx_type == "fx" and fx_id == "4":
            return "patch3 (temporary patch)"
        return "patch (temporary patch)"

    def _get_fx_start_section(self, fx_id, fx_name):
        table_suffix = FX_TO_TABLE_SUFFIX[fx_name]
        full_name = f"fx{fx_id}{table_suffix}"
        if full_name in [
            "fx1ChorusBass",
            "fx1FlangerBass",
            "fx2ChorusBass",
            "fx2FlangerBass",
            "fx3ChorusBass",
            "fx3FlangerBass",
        ]:
            return "patch2 (temporary patch)"
        elif str(fx_id) == "4":
            return "patch3 (temporary patch)"
        elif full_name in [
            "fx1Dist",
            "fx1MasterFx",
            "fx2Dist",
            "fx2MasterFx",
            "fx3Dist",
            "fx3MasterFx",
        ]:
            return "patch3 (temporary patch)"
        return "patch (temporary patch)"

    def toggle_fx_state(self, fx_type, fx_id, state):
        # Strip the number for blocks with only one instance
        if self.fx_types_count[fx_type] == 1:
            fx_id = ""
        elif fx_type == "preamp" and fx_id == 1:
            fx_id = "A"
        elif fx_type == "preamp" and fx_id == 2:
            fx_id = "B"
        self.send_message(
            self.build_dt_message(
                self._get_start_section(fx_type, fx_id),
                f"{fx_type}{fx_id}",
                "SW",
                state,
            )
        )

    def set_fx_value(self, fx_type, fx_id, option, value):
        # the sliders can want to send float
        value = int(value)
        # Strip the number for blocks with only one instance
        if self.fx_types_count[fx_type] == 1:
            fx_id = ""
        elif fx_type == "preamp" and fx_id == 1:
            fx_id = "A"
        elif fx_type == "preamp" and fx_id == 2:
            fx_id = "B"
        if fx_type == "fx":
            fx_name = self.current_fx_names[str(fx_id)]
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

    def send_message(self, message, offset=None):
        with self.data_semaphore:
            self.received_data[str(offset)] = None
            logger.debug(f"sending: {bytes_as_hex(message)}")
            self.midi_out.send_message(message)

    def _build_message(self, header, address_value, override_checksum=None):
        if override_checksum is not None:
            checksum = override_checksum
        else:
            checksum = self.calculate_checksum(address_value)
        # Override the broadcast address
        header[1] = self.device_id
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
        # logger.debug("receiving")
        if self.device_id == DEVICE_ID_BCAST and self._msg_identity_reply(message):
            logger.debug("identity ok")
            return
        received_data_header = (
            SYSEX_START + MANUFACTURER_ID + [self.device_id] + MODEL_ID + DT1_COMMAND_ID
        )
        for i in range(len(received_data_header)):
            if message[i] != received_data_header[i]:
                # logger.debug("Ignored received data")
                return
        received_offset = message[
            len(received_data_header) : len(received_data_header) + 4
        ]
        # The actual data is after the header and before the checksum + SYSEX_END
        self.received_data[str(received_offset)] = message[
            len(received_data_header) + 4 : -2
        ]
        logger.debug(
            f"data received: {self.received_data[str(received_offset)]} for offset {received_offset}"
        )

    def _construct_address_value(self, start_section, option, setting, param):
        # param is the setting we want to set, if None we just contruct the base address
        if start_section not in self.tables["base-addresses"]:
            logger.error(f"Entry {start_section} missing in base-addresses")
            return None

        section_entry = self.tables["base-addresses"][start_section]
        address = bytes_to_int(section_entry["address"])

        option_entry = self.tables[section_entry["table"]][option]
        option_address_offset = bytes_to_int(option_entry["address"])

        setting_entry = self.tables[option_entry["table"]][setting]
        setting_address_offset = bytes_to_int(setting_entry["offset"])

        address += option_address_offset + setting_address_offset
        if param is None:
            num_bytes = (address.bit_length() + 7) // 8
            byte_sequence = address.to_bytes(num_bytes, byteorder="big")
            byte_list = [byte for byte in byte_sequence]
            return byte_list

        # If we set the raw value
        if param not in setting_entry["values"]:
            value = param.to_bytes(1, byteorder="big")
        else:
            param_entry = setting_entry["values"][param]
            value = param_entry.to_bytes(1, byteorder="big")

        num_bytes = (address.bit_length() + 7) // 8
        byte_sequence = address.to_bytes(num_bytes, byteorder="big") + value
        byte_list = [byte for byte in byte_sequence]
        return byte_list

    def _lookup_value_range(self, start_section, option, setting):
        # param is the setting we want to set, if None we just contruct the base address
        if start_section not in self.tables["base-addresses"]:
            logger.error(f"Entry {start_section} missing in base-addresses")
            return None

        section_entry = self.tables["base-addresses"][start_section]

        option_entry = self.tables[section_entry["table"]][option]

        setting_entry = self.tables[option_entry["table"]][setting]
        return setting_entry["value_range"]
