"""Protocol constants for Glowrium (INLEDCO) LED lamps.

Every value here was established by observing a real device. Confidence is
recorded per property in PROPERTIES below, so callers can tell what is
verified from what is still inferred.

See docs/PROTOCOL.md for the wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------- BLE

# The UUID suffix 7261-6262-6974-696f74626c65 is ASCII for "rabbitiotble",
# a device BLE stack identifier. It is shared across INLEDCO products, so this
# service is likely not specific to the C045.
_SUFFIX = "-7261-6262-6974-696f74626c65"

SERVICE_UUID = f"facebd00{_SUFFIX}"

# Characteristic roles, confirmed by live GATT enumeration:
#
#   facebd01  write                  command channel
#   facebd02  read, write, notify    device state (CBOR property map) + status
#   facebd03  write, notify          believed to be OTA / firmware transfer
#   facebd80  read, write, notify    device identity string
#   facebd81  read                   protocol version byte (observed: 0x03)
CHAR_COMMAND = f"facebd01{_SUFFIX}"
CHAR_STATE = f"facebd02{_SUFFIX}"
CHAR_OTA = f"facebd03{_SUFFIX}"
CHAR_INFO = f"facebd80{_SUFFIX}"
CHAR_VERSION = f"facebd81{_SUFFIX}"

CHAR_UUIDS = {
    "facebd01": CHAR_COMMAND,
    "facebd02": CHAR_STATE,
    "facebd03": CHAR_OTA,
    "facebd80": CHAR_INFO,
    "facebd81": CHAR_VERSION,
}

CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# Advertising name pattern: "Glowrium-<hwrev>_<last 3 octets of BLE MAC>".
# On ESP32 the BLE MAC is the WiFi MAC + 2.
NAME_PREFIX = "Glowrium"

# Advertising service data under FACEBD00 carries the full BLE MAC at offset 2:
#   00 65 | 88 56 a6 f2 36 4e | 03 00 2d 01
ADV_MAC_OFFSET = 2
ADV_MAC_LENGTH = 6

# --------------------------------------------------------------- Colour model

# Colour entries are four bytes. Confirmed by the factory default preset, which
# paints each channel in turn down the lamp:
#   ff 00 00 00 / 00 ff 00 00 / 00 00 ff 00 / 00 00 00 ff, repeated five times.
# The fourth channel is a separate emitter, used by the lamp's own warm
# presets and to make white; it is not a computed mix of R, G and B.
CHANNELS = ("red", "green", "blue", "yellow")
CHANNEL_COUNT = len(CHANNELS)

# The lamp is a strip of 20 individually addressable segments, NOT a single
# light with one colour. Key 22 is spatial, not temporal: 20 slots x RGBY.
#
# Confirmed by painting R,G,B repeating from the top and reading back GRBGRB...
# The array is stored BOTTOM to TOP, so index 0 is the bottom segment:
#
#     array[i] == physical_from_top[SEGMENT_COUNT - 1 - i]
#
# This also re-explains the factory preset, which is a static rainbow down the
# lamp rather than the animation it looks like when read as a time series.
SEGMENT_COUNT = 20


def segment_index_from_top(position: int) -> int:
    """Map a 0-based segment counted from the top to its array index.

    The device stores segments bottom-first, which is the opposite of how
    anyone describes a standing lamp, so this conversion is easy to get wrong
    silently.
    """
    if not 0 <= position < SEGMENT_COUNT:
        raise ValueError(f"segment must be 0-{SEGMENT_COUNT - 1}, got {position}")
    return SEGMENT_COUNT - 1 - position


# ------------------------------------------------------------------ Properties

@dataclass(frozen=True)
class Property:
    """One integer-keyed property in the device's CBOR state map."""

    key: int
    name: str
    kind: str          # "bool" | "uint" | "int" | "bytes" | "array"
    confidence: str    # "confirmed" | "derived" | "unknown"
    note: str = ""
    writable: bool = False


# Keys observed in a live read of facebd02. Semantics range from certain (the
# timezone offset decodes to exactly UTC-8, matching the device's location) to
# entirely unidentified, and are labelled accordingly. Do not write to a
# property whose confidence is "unknown".
# The device's property table. Names and sizes are the device's own.
# `confidence` records how well the behaviour is understood, and `writable`
# means the write was confirmed on hardware, which is a higher bar than simply
# knowing a property's name.
PROPERTIES: dict[int, Property] = {p.key: p for p in [
    # --- confirmed working, verified visually on hardware -------------------
    Property(1, "Glm", "uint", "confirmed",
             "Global luminance, 0-100. Persists across power cycles.",
             writable=True),
    Property(2, "GPower", "bool", "confirmed",
             "Global power. Writing this turns the lamp on and off.",
             writable=True),
    Property(22, "Section0", "bytes", "confirmed",
             "80 bytes = 20 segments x RGBY, index 0 = bottom. The only "
             "property that has ever repainted the lamp.",
             writable=True),

    # --- confirmed by single-variable hardware experiments -------------------
    Property(3, "GTime", "bool", "confirmed",
             "Timer master enable. Went True when a timer was created; the "
             "timer's content lives in GTimeDat0 (key 4), not here."),
    Property(77, "Remind", "array", "confirmed",
             "Hourly chime: [enabled, start_seconds, end_seconds]. Write "
             "confirmed on hardware: set to 07:15-22:40 and read back exactly.",
             writable=True),
    Property(80, "RiseSlow", "array", "confirmed",
             "Gradual fade: [enabled, duration_seconds]. Confirmed visually: "
             "with 25 s set, the lamp fades out slowly on power-off instead of "
             "snapping off.",
             writable=True),
    Property(83, "timeoffset", "int", "confirmed",
             "Minutes from UTC. Observed -480, exactly UTC-8."),
    Property(84, "device_time", "uint", "confirmed",
             "Unix epoch in milliseconds."),
    Property(101, "version", "uint", "confirmed", "Observed 3."),

    # --- named by the device, meaning not independently verified ------------
    # ModeCtr reports the active mode faithfully but writing it provably does
    # nothing: the lamp kept pulsing to sound while reporting all-white.
    # Writing this ALONE does nothing, which an earlier experiment established
    # and this project then stated far too broadly. Written TOGETHER with
    # DyData (key 24) it completes a mode change: DyData alone left the lamp
    # half-applied, with the middle of the column flickering and the top stuck
    # on its previous colour, and adding ModeCtr produced a full campfire.
    #
    # So it is not a passive label. It is one half of a two-property write.
    Property(19, "ModeCtr", "bytes", "confirmed",
             "3 bytes identifying the mode. Must be written together with "
             "DyData (key 24); alone it has no effect.",
             writable=True),
    Property(8, "colorMode", "bytes", "derived",
             "4 bytes. Not a colour despite looking like RGBY; byte 3 tracks "
             "the mode (0x32 typical, 0x01 in music)."),
    Property(23, "LmArray", "bytes", "derived",
             "20 bytes, one luminance per segment, bottom-first like "
             "Section0. All 100 in every capture so far."),
    # The animation program. 20-byte header then 20 RGBY palette entries.
    #
    # Header bytes, from a custom mode built with known settings:
    #   byte 1  LED count: how many segments the effect spans. Confirmed
    #           behaviourally, not just by value: with byte 1 = 7, exactly 7
    #           LEDs animated and the other 13 stayed static.
    #   byte 3  brightness (100 in every capture)
    #   byte 4  PALETTE LENGTH: how many palette entries the device cycles.
    #           campfire declares 6 and carries 6 colours, torch 20 and 20.
    #           This was correctly identified, then wrongly retracted when a
    #           custom mode described as "three colours" read 20; that palette
    #           genuinely had 20 entries in use (7 red, 6 green, 7 blue), so
    #           the retraction was the error, not the original reading.
    #   byte 2  speed AND speed mode, packed:
    #             bits 0-6  101 - speed. Confirmed on three points,
    #                       100 -> 1, 23 -> 78, 50 -> 51, the last predicted.
    #             bit 7     speed mode. 0 = accelerate, 1 = uniform. Setting
    #                       uniform moved the byte 51 -> 179, i.e. 51 | 0x80,
    #                       leaving the speed bits untouched.
    #           Anything writing this byte must preserve the half it is not
    #           setting; build_dydata does.
    #   byte 0  dynamic type. Observed: 2 = Gradient, 3 = Expandable, 0 = the
    #           default the custom preset started on (probably Descent). Not a
    #           straight 0-indexed sequence, so Random and
    #           Marquee are not guessed at. Campfire and torch also read 3, so
    #           built-in presets are built on Expandable.
    #           byte 11 = 250 exactly when byte 0 = 3, across all nine
    #           captures, so byte 11 belongs to the type rather than to any
    #           user-facing control.
    #           NOT ORTHOGONAL: changing only this control also
    #           zeroed direction (6), tail (7), bytes 9 and 10, and set byte 11
    #           to 250 and byte 19 to 1. Parameter maps clear values
    #           that do not apply to the chosen dynamic type. Byte 11 = 250
    #           travels with byte 0 = 3 in every capture we have.
    #           Consequence: byte 0 cannot be safely overridden on its own, so
    #           build_dydata deliberately does not expose it. Change dynamic
    #           type on the device and capture the result as a new template.
    #   byte 5  response method. 0 = stacking, 1 = water drops under descent.
    #           Campfire reads 2 and torch 3, so like byte 6 it has more values
    #           than any one dynamic type exposes.
    #   byte 6  direction, but its meaning is RELATIVE TO THE DYNAMIC TYPE.
    #           Under one type, setting "upward" gave 1. Under Expandable,
    #           setting "downward expansion" also gave 1. The protocol relabels this
    #           control per dynamic type and maps the values differently, which
    #           also explains campfire reading 3 where a binary flag cannot.
    #           So a direction value is only meaningful alongside the byte 0 it
    #           was captured with.
    #   byte 8  colour mode. Three values observed, and they are NOT a
    #           sequence: cycle rotation = 0, random = 3, first = 4. The gap at
    #           1 and 2 suggests either a bitfield or an enum with members this
    #           dynamic type does not expose. No scheme is assumed; see
    #           COLOUR_MODES for the raw observations.
    #   byte 18 background brightness, 0-100. Observed 5 in captures.
    #   byte 7  tail length, 0-3. Isolated cleanly: changing only the
    #           tail-length control from 0 to 3 moved this byte and nothing
    #           else. Predicted as byte 6 beforehand, because campfire reads 3
    #           there and torch 0, which fit the range by coincidence.
    #   byte 9, 10  auxiliary values owned by the dynamic type: descent brings
    #           19 and 100.
    #   byte 12 unmapped, and the only header byte left. Campfire reads 5 and
    #           torch 12, both dynamic type 3, while every custom capture reads
    #           0. So it is a per-mode parameter within Expandable rather than
    #           anything the Descent UI exposes.
    #   bytes 13-17  zero in all sixteen captures taken.

    #
    # The LAST palette entry, index 19, holds the BACKGROUND COLOUR rather than
    # a segment colour. Setting the background to green changed only entry 19,
    # from blue to green, while the effect's own colours at 0-18 held. The
    # background fills segments the effect does not cover, which with an LED
    # count of 10 is the upper half.
    #
    # ORDERING RESOLVED: the palette is BOTTOM-first, the same convention as
    # Section0. With LED count 7 and palette[0..6] red, exactly 7 LEDs animated
    # at the BOTTOM of the column. An earlier worry that it might be top-first
    # is retracted; the two properties agree.
    Property(24, "DyData", "bytes", "confirmed",
             "20-byte header then a palette of up to 20 RGBY colours. Header "
             "byte 4 is the palette length. Write with ModeCtr to set a mode.",
             writable=True),
    # Semantics fell out of the write test: we sent [1, 0, 600, 600] and read
    # back [1, 0, 600, 594] a few seconds later. Element 3 is the live
    # remaining time, ticking down on the device.
    Property(5, "GCountdown", "array", "confirmed",
             "[enabled, ?, total_seconds, remaining_seconds]. Element 3 counts "
             "down live; element 1 is unidentified and was 0 throughout.",
             writable=True),
    Property(66, "preview", "uint", "unknown",
             "Latched 0 -> 1 on the first config change and stayed. Named "
             "'preview' by the device; role unclear."),

    # --- daylight saving, named by the device, never exercised --------------
    Property(86, "dst", "bool", "derived", "Daylight saving enable."),
    Property(87, "dst_start", "uint", "derived", "Daylight saving start."),
    Property(88, "dst_end", "uint", "derived", "Daylight saving end."),
    Property(89, "dst_offset", "uint", "derived", "Daylight saving offset."),

    # --- timer data slots ---------------------------------------------------
    # Five slots. The device declares 188 bytes each; reads return 112, an
    # unexplained discrepancy. Slot 0 is key 4 and never arrives in a read,
    # which is why a single timer's content was invisible to every experiment.
    Property(4, "GTimeDat0", "bytes", "derived",
             "Timer slot 0, 188B declared. NEVER returned by a state read."),
    Property(40, "GTimeDat1", "bytes", "derived",
             "Timer slot 1, 188B declared. NEVER returned by a state read."),
    Property(41, "GTimeDat2", "bytes", "derived",
             "Timer slot 2. 112B = 12B config + 100B DyData program."),
    Property(42, "GTimeDat3", "bytes", "derived", "Timer slot 3, as slot 2."),
    Property(43, "GTimeDat4", "bytes", "derived", "Timer slot 4, as slot 2."),

    # --- present in the device's table, never seen in any read --------------
    # Accepted a 4-byte zero write without error and WITHOUT an echo, unlike
    # every other property tried. Never read, never echoed, so its content has
    # never been observed and nothing about its meaning is established. The
    # device stayed healthy afterwards.
    Property(7, "baseAd", "bytes", "unknown",
             "4B. Never read and never echoed; accepts writes silently."),
    # Sunset and sunrise SCENE configuration: 2 config bytes followed by a
    # 100-byte DyData program, matching the declared 102. Writes are accepted
    # and echoed. The device echoes 106 bytes though, appending "40 40 00 7e"
    # of its own, so the declared size is the writable portion only.
    Property(62, "SCfg", "bytes", "derived",
             "Sunset scene: 2B config + 100B DyData program. Never read; "
             "verified by echo. Device appends 4 unexplained bytes."),
    Property(63, "RCfg", "bytes", "derived",
             "Sunrise scene, same layout as SCfg."),
    # Volunteered in the notification that followed a RiseSet write, so it is
    # related to sunrise/sunset. Observed 06 01 11 28, which reads as the
    # hour/minute pairs 06:01 and 17:40. Purpose unconfirmed.
    # Volunteered by the device in the echo of EVERY sunrise/sunset write:
    # RiseSet, SCfg and RCfg all bring it along, always reading 06 01 11 28.
    Property(65, "RStime", "bytes", "derived",
             "4B, seen only via notification, never written by us. Observed "
             "06 01 11 28, plausibly two hour/minute pairs. Accompanies every "
             "write in the sunrise/sunset family."),
    # CONFIRMED by notification echo rather than readback. Writing
    # {79: [1, 23400, 72900]} made the device push a notification carrying key
    # 79 with exactly those values, which is how a property past the read
    # truncation can still be verified.
    Property(79, "RiseSet", "array", "confirmed",
             "Sunrise/sunset schedule: [enabled, rise_seconds, set_seconds]. "
             "Never returned by a read; verified via the notification echo.",
             writable=True),
    # As baseAd: accepts a zero write, no echo, content never observed.
    Property(125, "extra", "bytes", "unknown",
             "10B. Never read and never echoed; accepts writes silently."),
]}

# Keys the device's own table declares but which no state read has ever
# returned. The read truncates at 738 bytes, and these fall past the cut.
#
# The truncation is device-side and cannot be fixed from the central.
#
# The negotiated ATT_MTU is 247, measured AFTER service discovery. Reading it
# in didConnect returns 23, the pre-negotiation default, and an earlier version
# of this comment recorded that wrong value and built a wrong explanation on
# it. Notifications of 128 bytes, impossible at MTU 23, are what exposed the
# error.
#
# At MTU 247 a read fetches 246 bytes per PDU and 738 = 3 * 246 exactly. The
# device serves three full PDUs and then stops, while its CBOR header declares
# 28 pairs and only 22 arrive. So the device's attribute is capped at 738 bytes
# regardless of how much its encoder produced. Why 738 specifically is
# unknown; it is a firmware-side buffer limit.
#
# WORKAROUND: writes are echoed back as notifications, and those notifications
# carry properties the read path never delivers. Six of the eight have been
# reached this way: 4, 40, 62, 63, 65 and 79.
#
# The echo is NOT universal. baseAd (7) and extra (125) accept writes without
# error and produce no notification at all, so their content remains
# unobserved by any channel.
NEVER_ECHOED = (7, 125)
NEVER_READ = (4, 7, 40, 62, 63, 65, 79, 125)

# How a declared size maps to the CBOR encoding on the wire.
#
# The device's table gives each property's raw struct size. Scalar-array
# properties hold 4-byte ints and encode as a CBOR array of size/4 elements;
# this is verified twice, and both times it predicted the observed value:
#
#     RiseSlow  8B  -> [enabled, duration]              observed [1, 10]
#     Remind   12B  -> [enabled, start, end]            observed [1, 21600, 64800]
#
# Byte-buffer properties encode as a CBOR byte string of exactly that size:
#
#     Section0 80B  = 20 segments x RGBY                observed
#     LmArray  20B  = 20 per-segment luminances         observed
#     DyData  100B  = 20B header + 20 segments x RGBY   observed
INT_SIZE = 4


def array_len(key: int) -> int:
    """Elements in a scalar-array property, from its declared size."""
    return DECLARED_SIZES[key] // INT_SIZE


# Declared payload sizes, from the device's property table.
DECLARED_SIZES = {
    1: 4, 2: 1, 3: 1, 4: 188, 5: 16, 7: 4, 8: 4, 19: 3, 22: 80, 23: 20,
    24: 100, 40: 188, 41: 188, 42: 188, 43: 188, 62: 102, 63: 102, 65: 4,
    66: 4, 77: 12, 79: 12, 80: 8, 83: 4, 84: 8, 86: 1, 87: 8, 88: 8, 89: 4,
    101: 4, 125: 10,
}

# Sunrise/sunset. "Rise" and "Set" are the device's own words: RiseSet holds
# the schedule, and RCfg/SCfg hold the scene played at each end. Each Cfg is
# 102 bytes = 2 bytes plus a 100-byte DyData-shaped program, matching
# the same structure that carries a lighting program.
KEY_RISESET = 79           # RiseSet
KEY_CHIME = 77             # Remind
KEY_GRADUAL = 80           # RiseSlow
KEY_COUNTDOWN = 5          # GCountdown
KEY_DYDATA = 24            # DyData, the animation program
KEY_RISE_CFG = 63
KEY_SET_CFG = 62

# Field order of the sunrise data structure. The field at offset 0x33 is
# unnamed.
SUNRISE_DATA_FIELDS = (
    "openType", "responseType", "stopType", "moodType",
    "<unnamed@0x33>", "minutes", "modeCtr0", "modeCtr1", "modeCtr2",
    "dynamicModel",
)

# How each dynamic type treats the palette and the background, learned by
# building a Matrix-rain effect and watching the lamp:
#
#   descent (0)   lights only the moving drop; the rest of the column shows the
#                 background, so a black background gives a dark column. One
#                 drop at a time. The palette is a COLOUR SEQUENCE for that
#                 drop, not a per-segment map.
#   marquee (5)   fills its whole span with a repeating pattern, so with
#                 led_count = 20 the background is never visible and black
#                 cannot be rendered at all. Several bands, never dark.
#
# Background only fills segments the effect does not occupy. That is why
# reducing led_count reveals it and led_count = 20 hides it.
#
# Dynamic types observed in DyData header byte 0. Only these three have been
# seen; the protocol also supports Random and Marquee, whose values are unknown.
# All five dynamic types are supported on hardware.
#
# The numbering is 0, 2, 3, 4, 5 with a GAP AT 1. Gradient/Expandable/Random/
# Marquee came out consecutive at 2-5, from which this project predicted
# Descent = 1 and, worse, concluded that byte 0 = 0 must therefore be a
# "none/static" value. Both were wrong: Descent is 0. The extrapolation was the
# second time a tidy pattern gave a wrong answer here.
#
# What 1 means, if anything, is unknown. It has never been observed.
DYNAMIC_TYPES = {
    "descent": 0,
    "gradient": 2,
    "expandable": 3,    # also what campfire and torch use
    "random": 4,
    "marquee": 5,
}
DYNAMIC_TYPES_OBSERVED = tuple(DYNAMIC_TYPES)

# Each dynamic type carries its own auxiliary header bytes, which
# are updated when the type changes. Observed pairings:
#   type 0 (descent)     byte 9 = 19, byte 10 = 100
#   type 3 (expandable)  byte 11 = 250
# These are why build_dydata takes a template instead of building a header.

# DyData header byte 8, colour mode. Observed values only. They skip 1 and 2,
# so this is not a plain index and the missing values are not guessed at.
COLOUR_MODES = {
    "cycle_rotation": 0,
    "random": 3,
    "first": 4,
}

# Timer item enums: which lamp section a timer targets, and what it does.
TIMER_MODE = {"Main": 100, "Mood": 101, "All": 102}
TIMER_TYPE = {"wakeUp": 2, "helpSleep": 3}

# WiFi status fields, keyed 0 in the table and presumably carried elsewhere.
WIFI_FIELDS = {"ssid": 15, "ip": 16, "rssi": 4}

# Convenience lookups.
PROPERTY_BY_NAME = {p.name: p for p in PROPERTIES.values()}
KEY_POWER = 2              # GPower
KEY_BRIGHTNESS = 1         # Glm, global luminance
KEY_COLOUR = 8             # colorMode (NOT a colour)
KEY_MODE = 19              # ModeCtr
KEY_SEGMENTS = 22          # Section0
KEY_SEGMENT_BRIGHTNESS = 23  # LmArray
KEY_TIMESTAMP = 84         # device_time

# Mode values observed on hardware, each captured while the lamp was visibly in
# that state. Recorded as raw bytes rather than decomposed, because three
# attempts at a structural reading have not survived the next capture.
MODE_VALUES = {
    "white": bytes.fromhex("010004"),
    "cycling": bytes.fromhex("010000"),
    "solid": bytes.fromhex("000006"),
    "segments": bytes.fromhex("000100"),
    "all_white": bytes.fromhex("000005"),
    "music_energy_core": bytes.fromhex("020007"),
    "unidentified": bytes.fromhex("010087"),
}

# --------------------------------------------------------------------- Cloud

# Recorded for completeness. Cloud control requires device credentials: the MQTT
# broker and credentials are provisioned per-device at runtime via
# `device/mqtt_param`. Note both endpoints are plain HTTP.
CLOUD_API = "http://iot.ledinpro.com:8080/api/"
CLOUD_API_ALT = "http://47.251.4.156:9008/api/"

# -------------------------------------------------------------------- Scenes

# The full C045 mode table lives in tests/data/c045_modes.txt: 62 modes, of
# which 27 carry an explicit numeric index (mode_1 .. mode_27) and 14 are music
# sub-modes.
#
# UNVERIFIED whether those indices ever reach the lamp. Writing ModeCtr
# (key 19) provably does nothing, which suggests the client instead pushes the
# program itself via DyData (key 24) and Section0 (key 22).
#
# The older SCENE_NAMES list below is a different, device-level set found in
# the binary; the numeric values it uses are still unknown.
SCENE_NAMES = (
    "SCENE_NONE", "SCENE_AMBIENT", "SCENE_AMBIENT_MODE", "SCENE_COUNTDOWN",
    "SCENE_FOCUS", "SCENE_LEARN", "SCENE_LEAVE_HOME", "SCENE_LIGHTNING",
    "SCENE_LIVE", "SCENE_MK_UP", "SCENE_MOVIE_MODE", "SCENE_READ_MODE",
    "SCENE_RELAX_MODE", "SCENE_SLEEP_MODE", "SCENE_WAKE_UP",
)
