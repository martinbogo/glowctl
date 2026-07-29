"""Open source control for Glowrium (INLEDCO) LED lamps.

The lamp is a strip of 20 individually addressable RGBY segments driven over
BLE with CBOR property maps. Power, brightness, per-segment colour, several
config features and full mode control all work and are confirmed on hardware.

    from glowctl import discover, LampTransport, protocol

    lamps = await discover()
    async with LampTransport(lamps[0].device) as lamp:
        await lamp.send_properties({2: True})           # power on
        await lamp.write(protocol.encode_colour(255, 0, 0))

See docs/PROTOCOL.md for the wire protocol and what remains unknown.
"""

from . import modes, protocol
from .const import (
    CHANNELS,
    COLOUR_MODES,
    NEVER_ECHOED,
    NEVER_READ,
    CHAR_UUIDS,
    DYNAMIC_TYPES,
    PROPERTIES,
    SEGMENT_COUNT,
    SERVICE_UUID,
    segment_index_from_top,
)
from .protocol import (
    UnsafeProperty,
    build_dydata,
    decode_state,
    encode_brightness,
    encode_chime,
    encode_colour,
    encode_countdown,
    encode_gradual,
    encode_mode,
    encode_power,
    encode_segment_colours,
    encode_sunrise_sunset,
    build_timer_slot,
    parse_dydata,
    parse_timer_slot,
    split_composite,
    parse_segments,
    segments_from_top,
)
from .transport import DiscoveredLamp, LampTransport, discover

__version__ = "1.0.0"

__all__ = [
    # transport
    "DiscoveredLamp",
    "LampTransport",
    "discover",
    # protocol
    "protocol",
    "decode_state",
    "encode_power",
    "encode_brightness",
    "encode_colour",
    "encode_segment_colours",
    "encode_chime",
    "encode_gradual",
    "encode_countdown",
    "encode_mode",
    "build_dydata",
    "build_timer_slot",
    "encode_sunrise_sunset",
    "parse_timer_slot",
    "split_composite",
    "parse_segments",
    "parse_dydata",
    "segments_from_top",
    "UnsafeProperty",
    # modes
    "modes",
    # constants
    "CHANNELS",
    "CHAR_UUIDS",
    "COLOUR_MODES",
    "NEVER_ECHOED",
    "NEVER_READ",
    "DYNAMIC_TYPES",
    "PROPERTIES",
    "SEGMENT_COUNT",
    "SERVICE_UUID",
    "segment_index_from_top",
    "__version__",
]
