"""The GT-1000 SysEx wire format.

``SysExCodec`` owns the framing knowledge that used to be scattered across
``GT1000``'s ``_build_message`` / ``build_dt_message`` / ``build_rq_message`` /
``assemble_message`` builders, ``calculate_checksum``, ``_msg_identity_reply``,
and the inline header match at the top of ``process_received_message``. Each of
those was shallow; none owned "the wire format" as a concept.

The codec is pure: bytes in, bytes out. Given the negotiated ``device_id`` it

- ``encode_dt1(device_id, payload)`` — frame a DT1 (set) message,
- ``encode_rq1(device_id, address, length)`` — frame an RQ1 (request) message,
- ``parse_data_reply(device_id, message)`` — split an inbound data reply into
  its ``(offset, data)``,
- ``parse_identity_reply(message)`` — read the model / device id out of an
  identity reply.

No threading, no rtmidi, no correlation state. It complements ``AddressMap``:
AddressMap answers *which bytes*, the codec answers *how to frame them* and
*how to read a frame back*.
"""

import logging
from typing import NamedTuple

from .constants import (
    DT1_COMMAND_ID,
    DT1_SYSEX_HEADER,
    GEN_INFO,
    GT1000_FAMILY,
    IDENTITY_REPLY,
    MANUFACTURER_ID,
    MODEL_ID,
    NON_RT_MSG,
    RQ1_SYSEX_HEADER,
    SYSEX_END,
    SYSEX_START,
)

logger = logging.getLogger(__name__)


class IdentityReply(NamedTuple):
    """Parsed identity reply. ``model`` is ``None`` when the software revision
    does not map to a known GT-1000 variant (the device id is still valid)."""

    device_id: int
    model: str | None


class SysExCodec:
    """Pure DT1/RQ1 framing and reply parsing for the GT-1000 SysEx protocol."""

    @staticmethod
    def calculate_checksum(data):
        total = sum(data) % 128
        return [128 - total]

    def _frame(self, device_id, header, payload, override_checksum=None):
        if override_checksum is not None:
            checksum = override_checksum
        else:
            checksum = self.calculate_checksum(payload)
        # Substitute our negotiated device id for the broadcast address without
        # mutating the shared module-level header constant.
        header = header[:1] + [device_id] + header[2:]
        return SYSEX_START + header + payload + checksum + SYSEX_END

    def encode_dt1(self, device_id, payload, override_checksum=None):
        """Frame a DT1 (set) message from an address-value payload."""
        return self._frame(device_id, DT1_SYSEX_HEADER, payload, override_checksum)

    def encode_rq1(self, device_id, address, length, override_checksum=None):
        """Frame an RQ1 (request) message from an address and a length."""
        return self._frame(
            device_id, RQ1_SYSEX_HEADER, address + length, override_checksum
        )

    def parse_data_reply(self, device_id, message):
        """Split an inbound DT1 data reply into ``(offset, data)``.

        Returns ``None`` when the frame is not a data reply addressed to us
        (wrong header or a different device id)."""
        header = (
            SYSEX_START + MANUFACTURER_ID + [device_id] + MODEL_ID + DT1_COMMAND_ID
        )
        if len(message) < len(header) + 4:
            return None
        for i in range(len(header)):
            if message[i] != header[i]:
                return None
        offset = message[len(header) : len(header) + 4]
        # The actual data is after the header + 4-byte offset and before the
        # checksum + SYSEX_END.
        data = message[len(header) + 4 : -2]
        return offset, data

    def parse_identity_reply(self, message):
        """Read the ``(device_id, model)`` out of an identity reply.

        Returns an :class:`IdentityReply`, or ``None`` when the frame is not a
        GT-1000 identity reply."""
        # Byte layout (F0 7E dev 06 02 41 4F 03 00 00 nn 00 vv 00 F7):
        # dev is the device id; nn/vv are software revision levels 1 and 3 that
        # together identify the model.
        if len(message) != 15:
            return None
        if not (
            message[0] == SYSEX_START[0]
            and message[1] == NON_RT_MSG[0]
            # message[2] is the device id
            and message[3] == GEN_INFO[0]
            and message[4] == IDENTITY_REPLY[0]
            and message[5] == MANUFACTURER_ID[0]
            and message[6] == GT1000_FAMILY[0]
            and message[7] == GT1000_FAMILY[1]
        ):
            return None
        device_id = message[2]
        software_rev_1 = message[10]
        software_rev_2 = message[12]
        if software_rev_1 == 0x00 and software_rev_2 == 0x01:
            logger.info("GT-1000 detected")
            model = "GT-1000"
        elif software_rev_1 == 0x01 and software_rev_2 == 0x01:
            logger.info("GT-1000L detected")
            model = "GT-1000L"
        elif software_rev_1 == 0x02 and software_rev_2 == 0x00:
            logger.info("GT-1000CORE detected")
            model = "GT-1000CORE"
        else:
            logger.warning(
                f"Unknown model detected: [{hex(software_rev_1)}, {hex(software_rev_2)}]"
            )
            model = None
        return IdentityReply(device_id, model)
