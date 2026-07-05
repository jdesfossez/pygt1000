"""Pin the chain device round trip directly.

``ChainCodec`` owns the whole effect-chain round trip: the byte-list address, the
int<->name conversion (through ``AddressMap``'s element map), and the pure
``parse_chain`` / ``serialize_chain``. The device dependency is injected as
``fetch`` / ``set`` callables — the same pattern ``BlockReader`` uses — so the
round trip is exercisable with no wire.
"""

import pytest

from pygt1000.address_map import AddressMap
from pygt1000.chain import ChainCodec, EffectBlock, ONE_BYTE

FX_TYPES = [
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


@pytest.fixture
def address_map():
    return AddressMap(FX_TYPES)


def make_codec(address_map, fetch_ints):
    """A ChainCodec whose injected ``fetch`` returns ``fetch_ints`` and whose
    injected ``set`` records the last payload it was handed."""
    sent = {}

    def fetch(offset, length):
        sent["fetch_offset"] = offset
        sent["fetch_length"] = length
        return list(fetch_ints)

    def send(address_value):
        sent["payload"] = address_value

    return ChainCodec(address_map, fetch, send), sent


def expected_byte_list(address_map):
    start = address_map.start_section("efct", "0")
    return address_map.address_for(start, "efct", "CHAIN ELEMENT1", None)


def test_read_names_maps_bytes_to_element_names(address_map):
    codec, sent = make_codec(address_map, [0, 1, 2])
    names = codec.read_names()
    assert names == [address_map.chain_element_name(i) for i in (0, 1, 2)]
    # It addressed the chain element through the map, one byte at a time.
    assert sent["fetch_offset"] == expected_byte_list(address_map)
    assert sent["fetch_length"] == ONE_BYTE


def test_read_returns_parsed_blocks(address_map):
    codec, _ = make_codec(address_map, [0, 1, 2])
    blocks = codec.read()
    assert all(isinstance(b, EffectBlock) for b in blocks)
    assert [b.name for b in blocks] == [
        address_map.chain_element_name(i) for i in (0, 1, 2)
    ]


def test_write_names_maps_names_to_bytes_and_prefixes_address(address_map):
    codec, sent = make_codec(address_map, [])
    names = [address_map.chain_element_name(i) for i in (0, 1, 2)]
    codec.write_names(names)
    assert sent["payload"] == expected_byte_list(address_map) + [0, 1, 2]


def test_write_serializes_blocks_then_maps_to_bytes(address_map):
    codec, sent = make_codec(address_map, [])
    names = [address_map.chain_element_name(i) for i in (0, 1, 2)]
    codec.write([EffectBlock(n) for n in names])
    assert sent["payload"] == expected_byte_list(address_map) + [0, 1, 2]


def test_byte_to_object_to_byte_round_trip(address_map):
    raw = [0, 1, 2, 3, 4]
    codec, sent = make_codec(address_map, raw)
    blocks = codec.read()
    codec.write(blocks)
    assert sent["payload"] == expected_byte_list(address_map) + raw
