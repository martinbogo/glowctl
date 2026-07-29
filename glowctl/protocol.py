"""CBOR property protocol for the Glowrium C045.

The device does not use a byte frame with an opcode and checksum, which is what
most cheap LED controllers do. It uses **CBOR maps with integer keys**, and the
same key space serves both the BLE and cloud transports.

Evidence, from a live read of the facebd02 characteristic:

    b8 1c ...          CBOR map, 28 pairs
    18 54 1b ...       key 84 -> uint64 1785205150826, a millisecond epoch
    18 53 39 01 df     key 83 -> int -480, exactly UTC-8
    18 65 03           key 101 -> 3, matching the device's reported version

and from its status notifications:

    a2 18 54 1b ... 02 f5      map(2) {84: <timestamp>, 2: true}

Reading state is fully implemented and safe. Writing is implemented for the
three properties with enough evidence behind them, and deliberately refuses
anything whose meaning is still unknown, since a stray write to a configuration
key on a device that also exposes an OTA characteristic is not a cheap mistake.
"""

from __future__ import annotations

import time

import cbor2

from . import const


class UnsafeProperty(ValueError):
    """Raised when asked to write a property whose meaning is not established."""


# ------------------------------------------------------------------- decoding

def decode_state(data: bytes) -> dict[int, object]:
    """Decode a CBOR property map read from the state characteristic.

    A long read can be cut short by the transport, leaving a valid prefix of an
    incomplete map. Rather than lose everything, fall back to decoding pair by
    pair and return what did arrive.
    """
    try:
        value = cbor2.loads(data)
        if isinstance(value, dict):
            return value
        raise ValueError(f"expected a CBOR map, got {type(value).__name__}")
    except Exception:
        return _decode_partial_map(data)


def _decode_partial_map(data: bytes) -> dict[int, object]:
    """Decode as many key/value pairs as the buffer actually contains."""
    if not data:
        return {}
    head = data[0]
    if head >> 5 != 5:
        raise ValueError(f"not a CBOR map: leading byte 0x{head:02x}")

    ai = head & 0x1F
    off = 1
    if ai < 24:
        count = ai
    elif ai == 24:
        count, off = data[1], 2
    elif ai == 25:
        count, off = int.from_bytes(data[1:3], "big"), 3
    else:
        raise ValueError(f"unsupported map header 0x{head:02x}")

    out: dict[int, object] = {}
    for _ in range(count):
        try:
            key, off = _decode_item(data, off)
            val, off = _decode_item(data, off)
        except (IndexError, ValueError):
            break
        out[key] = val
    return out


def _decode_item(data: bytes, off: int) -> tuple[object, int]:
    """Decode one CBOR item, returning it and the new offset."""
    if off >= len(data):
        raise IndexError("truncated")
    ib = data[off]
    mt, ai = ib >> 5, ib & 0x1F
    off += 1

    if ai < 24:
        arg = ai
    elif ai == 24:
        arg, off = data[off], off + 1
    elif ai == 25:
        arg, off = int.from_bytes(data[off:off + 2], "big"), off + 2
    elif ai == 26:
        arg, off = int.from_bytes(data[off:off + 4], "big"), off + 4
    elif ai == 27:
        arg, off = int.from_bytes(data[off:off + 8], "big"), off + 8
    else:
        raise ValueError(f"bad additional info {ai}")

    if mt == 0:
        return arg, off
    if mt == 1:
        return -1 - arg, off
    if mt in (2, 3):
        end = off + arg
        if end > len(data):
            raise IndexError("truncated")
        chunk = data[off:end]
        return (chunk if mt == 2 else chunk.decode("utf-8", "replace")), end
    if mt == 4:
        items = []
        for _ in range(arg):
            item, off = _decode_item(data, off)
            items.append(item)
        return items, off
    if mt == 5:
        d = {}
        for _ in range(arg):
            k, off = _decode_item(data, off)
            v, off = _decode_item(data, off)
            d[k] = v
        return d, off
    if mt == 7:
        return {20: False, 21: True, 22: None}.get(arg, f"simple({arg})"), off
    raise ValueError(f"unsupported major type {mt}")


def describe_state(state: dict[int, object]) -> list[str]:
    """Render a decoded state map as annotated, human-readable lines."""
    lines = []
    for key in sorted(state):
        prop = const.PROPERTIES.get(key)
        value = state[key]
        name = prop.name if prop else f"key_{key}"
        conf = prop.confidence if prop else "unseen"

        if isinstance(value, bytes):
            shown = _describe_bytes(key, value)
        elif key == const.KEY_TIMESTAMP and isinstance(value, int):
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value / 1000))
            shown = f"{value} ({stamp})"
        elif key == 83 and isinstance(value, int):
            shown = f"{value} min (UTC{value // 60:+d})"
        else:
            shown = repr(value)

        lines.append(f"  {key:>3}  {name:<20} [{conf:<9}] {shown}")
    return lines


def _describe_bytes(key: int, value: bytes) -> str:
    """Summarise a byte-array property, unpacking known program layouts."""
    n = len(value)
    if key == const.KEY_COLOUR and n == const.CHANNEL_COUNT:
        r, g, b, y = value
        return f"R={r} G={g} B={b} Y={y}"
    if n == 80:
        segs = parse_segments(value)
        uniq = set(segs)
        if len(uniq) == 1:
            r, g, b, y = segs[0]
            return f"all {len(segs)} segments R={r} G={g} B={b} Y={y}"
        return f"{len(segs)} segments x RGBY, {len(uniq)} distinct"
    if n == 100:
        return f"bytes({n}) = 20B header + 20 segments x RGBY"
    if n == 112:
        return f"bytes({n}) = 32B header + 20 segments x RGBY"
    if n <= 16:
        return f"bytes({n}) {value.hex(' ')}"
    return f"bytes({n}) {value[:12].hex(' ')} ..."


def parse_segments(value: bytes, header_len: int = 0) -> list[tuple[int, ...]]:
    """Split a segment property into RGBY tuples, in the device's own order.

    Device order is bottom to top, so index 0 is the bottom segment. Use
    `segments_from_top` if you want them the way a person would describe them.
    """
    body = value[header_len:]
    return [tuple(body[i:i + 4]) for i in range(0, len(body) - 3, 4)]


def segments_from_top(value: bytes, header_len: int = 0) -> list[tuple[int, ...]]:
    """Segment colours ordered top to bottom, as seen looking at the lamp."""
    return list(reversed(parse_segments(value, header_len)))


def encode_segments(colours, *, from_top: bool = True) -> bytes:
    """Build the key 22 payload from per-segment RGBY colours.

    Args:
        colours: SEGMENT_COUNT tuples of (r, g, b, y), each 0-255.
        from_top: if True (the default) `colours` is ordered top to bottom the
            way a person would describe the lamp, and gets reversed into the
            device's bottom-first order. Pass False if already device-ordered.
    """
    colours = list(colours)
    if len(colours) != const.SEGMENT_COUNT:
        raise ValueError(
            f"need exactly {const.SEGMENT_COUNT} segments, got {len(colours)}"
        )
    out = bytearray()
    for colour in (reversed(colours) if from_top else colours):
        if len(colour) != const.CHANNEL_COUNT:
            raise ValueError(f"each segment needs {const.CHANNEL_COUNT} channels")
        for v in colour:
            if not 0 <= v <= 255:
                raise ValueError(f"channel values must be 0-255, got {v}")
            out.append(v)
    return bytes(out)


def encode_solid(red: int, green: int, blue: int, yellow: int = 0) -> bytes:
    """Build a key 22 payload with every segment the same colour.

    This is how a solid colour is represented on the device: not a special mode,
    just all 20 segments holding the same value.
    """
    return encode_segments([(red, green, blue, yellow)] * const.SEGMENT_COUNT)


def parse_identity(data: bytes) -> dict[str, str]:
    """Parse the semicolon-delimited identity string from the info characteristic.

    Observed:
        brand:GLOWRIUM;pkey:Glowrium-C045;subid:1;devid:ESP-8856A6F2364C;
        mac:8856A6F2364C;version:3;;
    """
    text = data.decode("utf-8", "replace")
    out = {}
    for field in text.split(";"):
        if ":" in field:
            k, _, v = field.partition(":")
            out[k.strip()] = v.strip()
    return out


# ------------------------------------------------------------------- encoding

def encode_properties(props: dict[int, object], *,
                      allow_unsafe: bool = False) -> bytes:
    """Encode a property map for the command characteristic.

    Refuses keys whose meaning has not been established, unless explicitly
    overridden. That guard exists because this device exposes an OTA
    characteristic and a large configuration surface, so a mistyped key is not
    a harmless no-op.
    """
    if not allow_unsafe:
        for key in props:
            prop = const.PROPERTIES.get(key)
            if prop is None:
                raise UnsafeProperty(
                    f"key {key} was not seen in the device state and its meaning "
                    f"is unknown; pass allow_unsafe=True if you are deliberately "
                    f"probing"
                )
            if not prop.writable:
                raise UnsafeProperty(
                    f"key {key} ({prop.name}) is not established as safely "
                    f"writable (confidence: {prop.confidence}); pass "
                    f"allow_unsafe=True if you are deliberately probing"
                )
    return cbor2.dumps(props)


def encode_power(on: bool) -> bytes:
    """Frame to turn the lamp on or off."""
    return encode_properties({const.KEY_POWER: bool(on)})


def encode_brightness(level: int) -> bytes:
    """Frame to set overall brightness, 0-100."""
    if not 0 <= level <= 100:
        raise ValueError(f"brightness must be 0-100, got {level}")
    return encode_properties({const.KEY_BRIGHTNESS: int(level)})


def encode_colour(red: int, green: int, blue: int, yellow: int = 0) -> bytes:
    """Frame to set the whole lamp to one RGBY colour, each channel 0-255.

    This writes key 22, painting every segment the same colour, because that is
    how the device actually represents a solid colour. It deliberately does not
    touch key 8: that key stores a colour reliably but never renders, which
    cost a full debugging cycle to discover.
    """
    return encode_properties(
        {const.KEY_SEGMENTS: encode_solid(red, green, blue, yellow)}
    )


def encode_segment_colours(colours, *, from_top: bool = True) -> bytes:
    """Frame to set each segment individually.

    `colours` is SEGMENT_COUNT RGBY tuples, ordered top to bottom by default.
    """
    return encode_properties(
        {const.KEY_SEGMENTS: encode_segments(colours, from_top=from_top)}
    )


def _encode_int_array(key: int, values) -> bytes:
    """Encode a scalar-array property, checking length against its declared size.

    The device's table gives each such property's size in bytes at 4 bytes per
    element, so a wrong-length array is caught here rather than on the wire.
    """
    values = [int(v) for v in values]
    expected = const.array_len(key)
    if len(values) != expected:
        raise ValueError(
            f"key {key} ({const.PROPERTIES[key].name}) takes {expected} "
            f"elements, got {len(values)}"
        )
    if any(v < 0 for v in values):
        raise ValueError("array elements must be non-negative")
    return encode_properties({key: values})


def encode_chime(enabled: bool, start_seconds: int = 6 * 3600,
                 end_seconds: int = 18 * 3600) -> bytes:
    """Hourly chime: active between two times of day, in seconds since midnight."""
    for t in (start_seconds, end_seconds):
        if not 0 <= t < 86400:
            raise ValueError(f"times must be 0-86399 seconds, got {t}")
    return _encode_int_array(const.KEY_CHIME, [int(bool(enabled)), start_seconds,
                                               end_seconds])


def encode_gradual(enabled: bool, duration_seconds: int = 10) -> bytes:
    """Gradual fade applied to on/off transitions."""
    if not 0 <= duration_seconds <= 0xFFFF:
        raise ValueError(f"duration out of range: {duration_seconds}")
    return _encode_int_array(const.KEY_GRADUAL,
                             [int(bool(enabled)), duration_seconds])


def encode_countdown(enabled: bool, seconds: int = 0) -> bytes:
    """Countdown timer.

    Element 3 is the device's live remaining time; we set it equal to the total
    so the countdown starts from the full duration.
    """
    if not 0 <= seconds <= 0xFFFFFF:
        raise ValueError(f"seconds out of range: {seconds}")
    return _encode_int_array(const.KEY_COUNTDOWN,
                             [int(bool(enabled)), 0, seconds, seconds])


def encode_sunrise_sunset(enabled: bool, rise_seconds: int,
                          set_seconds: int) -> bytes:
    """Sunrise/sunset schedule: [enabled, rise_seconds, set_seconds].

    Confirmed by notification echo. Key 79 is never returned by a state read,
    but writing it makes the device push a notification carrying the value
    back, which is how this was verified.
    """
    for t in (rise_seconds, set_seconds):
        if not 0 <= t < 86400:
            raise ValueError(f"times must be 0-86399 seconds, got {t}")
    return _encode_int_array(const.KEY_RISESET,
                             [int(bool(enabled)), rise_seconds, set_seconds])


def encode_mode(modectr: bytes, dydata: bytes) -> bytes:
    """Frame that applies a lighting mode.

    Both halves go in a single write. DyData alone leaves the lamp
    half-applied and ModeCtr alone does nothing, so they are not offered
    separately.
    """
    if len(modectr) != const.DECLARED_SIZES[const.KEY_MODE]:
        raise ValueError(f"ModeCtr must be {const.DECLARED_SIZES[const.KEY_MODE]} bytes")
    if len(dydata) != const.DECLARED_SIZES[const.KEY_DYDATA]:
        raise ValueError(f"DyData must be {const.DECLARED_SIZES[const.KEY_DYDATA]} bytes")
    return encode_properties({const.KEY_MODE: modectr, const.KEY_DYDATA: dydata})


def parse_dydata(value: bytes) -> dict:
    """Split a DyData program into its header fields and palette."""
    header = value[:20]
    palette = parse_segments(value[20:])
    speed, speed_mode = decode_speed(header[DY_SPEED])
    return {
        "dynamic_type": header[0],
        "led_count": header[DY_LED_COUNT],
        "speed": speed,
        "speed_mode": speed_mode,
        "brightness": header[DY_BRIGHTNESS],
        "segments": header[DY_SEGMENTS],
        "direction": header[DY_DIRECTION],
        "tail_length": header[DY_TAIL],
        "response_method": header[DY_RESPONSE],
        "colour_mode": header[DY_COLOUR_MODE],
        "background_brightness": header[DY_BACKGROUND],
        "background_colour": palette[-1] if palette else None,
        "header": header,
        "palette": palette,
    }


# DyData header offsets whose meaning was isolated by single-variable changes
# on hardware. Every other header byte is left as the template had it,
# because writing a guessed value into an unmapped field is how this project
# has gone wrong before.
DY_LED_COUNT = 1
DY_SPEED = 2          # bits 0-6: 101 - speed; bit 7: speed mode
DY_BRIGHTNESS = 3
DY_SEGMENTS = 4
DY_DIRECTION = 6      # 0 = downward, 1 = upward
DY_TAIL = 7           # 0-3
DY_RESPONSE = 5       # 0 stacking, 1 water drops (more values exist per type)
DY_COLOUR_MODE = 8    # see const.COLOUR_MODES
DY_BACKGROUND = 18    # background brightness 0-100
DY_SPEED_BASE = 101
DY_SPEED_MASK = 0x7F
DY_SPEED_MODE_BIT = 0x80    # set = uniform, clear = accelerate


def decode_speed(header_byte: int) -> tuple[int, int]:
    """Split header byte 2 into (speed 0-100, speed_mode 0/1)."""
    return (DY_SPEED_BASE - (header_byte & DY_SPEED_MASK),
            1 if header_byte & DY_SPEED_MODE_BIT else 0)


def build_dydata(template: bytes, *, palette=None, led_count=None, speed=None,
                 brightness=None, direction=None, tail_length=None,
                 colour_mode=None, background_brightness=None,
                 speed_mode=None, response_method=None,
                 palette_length=None) -> bytes:
    """Build a DyData program by overriding known fields of a captured one.

    A template is required rather than optional. Eight of the twenty header
    bytes still have no known meaning, and synthesising them from nothing would
    mean inventing values for fields we cannot check. Starting from a real
    captured program keeps those bytes at values the device is known to accept.

    Dynamic type (header byte 0) is deliberately NOT exposed. Changing it
    also rewrites six other header bytes, apparently clearing
    parameters that do not apply to the new type, so setting it in isolation
    would produce an invalid combination. To change dynamic
    type, capture the result from a verified template.

    Args:
        template: a captured 100-byte DyData program to start from.
        palette: up to SEGMENT_COUNT RGBY tuples, bottom-first, padded with
            black. Same ordering as Section0.
        led_count: how many segments the effect spans, 1-20.
        speed: 0-100. Stored inverted in bits 0-6 as 101 - speed.
        speed_mode: 0 accelerate, 1 uniform. Packed into bit 7 of the same
            byte as speed, which is why setting one must preserve the other.
        palette_length: how many palette entries the device cycles, 1-20.
            Campfire declares 6 and carries 6 colours; torch declares 20 and
            carries 20. Leave unset to keep the template's value.
        response_method: 0 stacking, 1 water drops. Like direction, more
            values exist than a single dynamic type exposes, so this is only
            meaningful relative to the template.
        brightness: 0-100.
        direction: 0 or 1, but its MEANING depends on the template's dynamic
            type (header byte 0). The protocol interprets this control per type: 1
            meant "upward" under one type and "downward expansion" under
            Expandable. Only meaningful relative to the template you passed.
        tail_length: 0-3.
    """
    if len(template) != const.DECLARED_SIZES[const.KEY_DYDATA]:
        raise ValueError(
            f"template must be {const.DECLARED_SIZES[const.KEY_DYDATA]} bytes, "
            f"got {len(template)}"
        )
    out = bytearray(template)

    def _set(offset, value, lo, hi, label):
        if value is None:
            return
        if not lo <= value <= hi:
            raise ValueError(f"{label} must be {lo}-{hi}, got {value}")
        out[offset] = value

    _set(DY_LED_COUNT, led_count, 1, const.SEGMENT_COUNT, "led_count")
    _set(DY_BRIGHTNESS, brightness, 0, 100, "brightness")
    _set(DY_DIRECTION, direction, 0, 1, "direction")
    _set(DY_TAIL, tail_length, 0, 3, "tail_length")
    _set(DY_SEGMENTS, palette_length, 1, const.SEGMENT_COUNT, "palette_length")
    _set(DY_RESPONSE, response_method, 0, 255, "response_method")
    _set(DY_COLOUR_MODE, colour_mode, 0, 255, "colour_mode")
    _set(DY_BACKGROUND, background_brightness, 0, 100, "background_brightness")

    # Speed and speed mode share byte 2, so each must preserve the other.
    if speed is not None:
        if not 0 <= speed <= 100:
            raise ValueError(f"speed must be 0-100, got {speed}")
        out[DY_SPEED] = (out[DY_SPEED] & DY_SPEED_MODE_BIT) | (DY_SPEED_BASE - speed)
    if speed_mode is not None:
        if speed_mode not in (0, 1):
            raise ValueError(f"speed_mode must be 0 or 1, got {speed_mode}")
        out[DY_SPEED] = (out[DY_SPEED] & DY_SPEED_MASK) | (
            DY_SPEED_MODE_BIT if speed_mode else 0)

    if palette is not None:
        colours = list(palette)
        if len(colours) > const.SEGMENT_COUNT:
            raise ValueError(f"at most {const.SEGMENT_COUNT} palette entries")
        colours += [(0, 0, 0, 0)] * (const.SEGMENT_COUNT - len(colours))
        out[20:] = encode_segments(colours, from_top=False)

    return bytes(out)


# Composite properties that embed a 100-byte DyData program.
#
#   GTimeDat (keys 4, 40-43)   12 B timer config + 100 B program  = 112
#   SCfg / RCfg (keys 62, 63)   2 B config       + 100 B program  = 102
#
# Confirmed for GTimeDat: splitting a real slot at offset 12 yields a program
# whose header carries the DyData signature (brightness 100, segments 20) and
# whose palette holds 20 well-formed RGBY entries. The SCfg/RCfg split is
# arithmetic only, since neither property is ever returned by a read.
GTIMEDAT_CONFIG_LEN = 12
CFG_CONFIG_LEN = 2


def split_composite(value: bytes, config_len: int) -> tuple[bytes, bytes]:
    """Split a composite property into (config prefix, DyData program)."""
    program_len = const.DECLARED_SIZES[const.KEY_DYDATA]
    if len(value) != config_len + program_len:
        raise ValueError(
            f"expected {config_len + program_len} bytes, got {len(value)}"
        )
    return value[:config_len], value[config_len:]


# Timer config byte offsets. Hour and minute are confirmed: a timer set to
# 19:45 read back 19 and 45 at offsets 1 and 2. The rest come from diffing an
# enabled slot against two empty ones.
# Byte 0 packs the enable bit with a weekday bitmask.
# "Once" reads 0x80 (enabled, no days); "daily" reads 0xff (enabled, all seven).
TIMER_FLAGS = 0         # bit 7 enable, bits 0-6 weekday mask
TIMER_HOUR = 1
TIMER_MINUTE = 2
TIMER_ACTION = 3        # 1 = on, 0 = off
TIMER_FLAG10 = 10       # 0x80 whenever the slot is in use; meaning unknown
TIMER_ENABLE_BIT = 0x80
TIMER_DAYS_MASK = 0x7F
TIMER_DAILY = 0x7F      # all seven weekday bits

# Retained for callers written against the earlier name.
TIMER_ENABLE = TIMER_FLAGS


def parse_timer_slot(value: bytes) -> dict:
    """Split a GTimeDat slot and decode the parts of its config we know.

    Hour and minute are solid. `action` and `flag10` move with the enable bit
    but their meanings are not established, and bytes 4-9 and 11 held identical
    values in every slot seen, enabled or not.
    """
    config, program = split_composite(value, GTIMEDAT_CONFIG_LEN)
    flags = config[TIMER_FLAGS]
    days = flags & TIMER_DAYS_MASK
    return {
        "enabled": bool(flags & TIMER_ENABLE_BIT),
        "days": days,
        "repeat": "daily" if days == TIMER_DAILY else
                  "once" if days == 0 else f"mask 0x{days:02x}",
        "hour": config[TIMER_HOUR],
        "minute": config[TIMER_MINUTE],
        "time": f"{config[TIMER_HOUR]:02d}:{config[TIMER_MINUTE]:02d}",
        "action": "on" if config[TIMER_ACTION] == 1 else "off",
        "config": config,
        "program": program,
        "parsed_program": parse_dydata(program),
    }


def build_timer_slot(template: bytes, *, hour=None, minute=None, enabled=None,
                     days=None, action=None) -> bytes:
    """Edit a captured timer slot, preserving everything not named.

    Like build_dydata this takes a template rather than building from nothing:
    config bytes 4-9 and 11 held identical values in every slot observed and
    have no known meaning, and byte 10 carries 0x80 whenever a slot is in use.

    Args:
        template: a captured 112-byte GTimeDat slot.
        hour, minute: 0-23 and 0-59.
        enabled: sets or clears bit 7 of the flags byte.
        days: weekday bitmask 0-0x7f. 0 is "once", TIMER_DAILY is every day.
        action: "on" or "off".
    """
    config, program = split_composite(template, GTIMEDAT_CONFIG_LEN)
    out = bytearray(config)

    if hour is not None:
        if not 0 <= hour <= 23:
            raise ValueError(f"hour must be 0-23, got {hour}")
        out[TIMER_HOUR] = hour
    if minute is not None:
        if not 0 <= minute <= 59:
            raise ValueError(f"minute must be 0-59, got {minute}")
        out[TIMER_MINUTE] = minute
    if days is not None:
        if not 0 <= days <= TIMER_DAYS_MASK:
            raise ValueError(f"days must be 0-0x7f, got {days:#x}")
        out[TIMER_FLAGS] = (out[TIMER_FLAGS] & TIMER_ENABLE_BIT) | days
    if enabled is not None:
        out[TIMER_FLAGS] = (out[TIMER_FLAGS] & TIMER_DAYS_MASK) | (
            TIMER_ENABLE_BIT if enabled else 0)
        out[TIMER_FLAG10] = TIMER_ENABLE_BIT if enabled else 0
    if action is not None:
        if action not in ("on", "off"):
            raise ValueError(f"action must be 'on' or 'off', got {action!r}")
        out[TIMER_ACTION] = 1 if action == "on" else 0

    return bytes(out) + program


# American spelling alias, since the CLI and most callers will reach for it.
encode_color = encode_colour
