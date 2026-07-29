# Glowrium C045 Protocol Specification

A description of the Glowrium lamp control protocol, for building interoperable
clients. Everything here was established by observing a real device: network
probing, GATT enumeration, and controlled experiments against the hardware.

Status legend: **[CONFIRMED]** observed directly · **[DERIVED]** inferred from
strong evidence, not yet proven · **[UNCERTAIN]** evidence is mixed ·
**[UNKNOWN]** still open.

---

## 1. Device identification

| Property | Value | Status |
|---|---|---|
| Product Name | Glowrium H4 Smart LED Corner Floor Lamp | [CONFIRMED] |
| Model Designation | Glowrium-C045 | [CONFIRMED] |
| Light Output | 1440 Lumens | [CONFIRMED] |
| Power Rating | 36 Watts | [CONFIRMED] |
| Illumination Angle | 160° Wide-Angle Glow | [CONFIRMED] |
| Color Temp Range | 2000K (Warm Yellow) to 7500K (Daylight) | [CONFIRMED] |
| WiFi MAC | `88:56:A6:F2:36:4C` | [CONFIRMED] |
| BLE MAC | `88:56:A6:F2:36:4E` | [CONFIRMED] |
| MAC OUI | `88:56:A6` = Espressif Inc. | [CONFIRMED] |
| LAN address | `192.168.0.81` (DHCP) | [CONFIRMED] |
| BLE advertised name | `Glowrium-H4_F2364E` | [CONFIRMED] |
| SoC family | ESP32 (classic, dual-mode) | [CONFIRMED] |
| Protocol version | 3 | [CONFIRMED] |

The device reports its own identity as a semicolon-delimited string on
characteristic `facebd80`:

```
brand:GLOWRIUM;pkey:Glowrium-C045;subid:1;devid:ESP-8856A6F2364C;
mac:8856A6F2364C;version:3;;
```

`devid` begins with `ESP-`, confirming the Espressif inference outright. The
BLE MAC is the WiFi MAC + 2, the ESP32 signature, and is also carried verbatim
in the advertising service data (below). `-H4` in the advertising name is a
hardware revision tag, distinct from the `C045` model code.

### Advertising payload [CONFIRMED]

Service data under `FACEBD00`:

```
00 65 | 88 56 a6 f2 36 4e | 03 00 2d 01
       \_____ BLE MAC ____/
```

The leading `00 65` and trailing `03 00 2d 01` are not fully identified; the
`03` plausibly matches the protocol version reported elsewhere.

---

## 2. Transport survey

### 2.1 WiFi: no local control surface [CONFIRMED]

A full TCP sweep of `192.168.0.81` (ports 1-1024 plus all common IoT/LED
controller ports: 5577 Magic Home, 6668 Tuya, 6053 ESPHome, 1883/8883 MQTT,
8266 ESP OTA, 9999 Kasa, 80/443/8080) returned **zero open ports**.

The lamp is not a server. It makes an **outbound** connection to a cloud MQTT
broker. **There is no local WiFi control path**; local control must use BLE.

### 2.2 Cloud [DERIVED]

| Item | Value |
|---|---|
| REST API | `http://iot.ledinpro.com:8080/api/` |
| REST API (alt/IP) | `http://47.251.4.156:9008/api/` |
| MQTT credentials | fetched per-user via `device/mqtt_param` |

The broker address and credentials are provisioned per account at runtime, not
hardcoded, so cloud control requires registered device credentials and is unsuitable as the
basis for an open client. Both API endpoints are plain HTTP, not HTTPS.

### 2.3 BLE GATT layout [CONFIRMED]

Service `facebd00-7261-6262-6974-696f74626c65`. The UUID suffix is ASCII:
`72 61 62 62 69 74 69 6f 74 62 6c 65` decodes to `rabbitiotble`. This is a
device BLE stack identifier rather than a Glowrium-specific one, so the same
protocol is likely shared across other INLEDCO products (including C011, C017, C037, C041, C045, C063, C066, C086, C094, 469).

| Characteristic | Properties | Role |
|---|---|---|
| `facebd01` | write | command channel |
| `facebd02` | read, write, notify | device state map + status notifications |
| `facebd03` | write, notify | believed OTA / firmware transfer |
| `facebd80` | read, write, notify | identity string |
| `facebd81` | read | protocol version byte (observed `03`) |

No pairing, bonding, or authentication handshake was required: an unbonded
central could connect, read state, and subscribe immediately. That is
convenient for us and a meaningful security weakness in the product.

**The lamp only advertises while nothing is connected to it.** When another active
connection exists, it is invisible to other scanners. This was the initial
blocker and is worth knowing before debugging a "missing" device.

---

## 3. The wire protocol is CBOR [CONFIRMED]

The device does **not** use a byte frame with an opcode and checksum, which is
what most cheap LED controllers do. It exchanges **CBOR maps keyed by small
integers**. This matches standard CBOR property maps and
the property get/set/post model in its MQTT layer, which suggests one key
space serves both the BLE and cloud transports.

Reading `facebd02` returns the full state map. The header and first pairs:

```
b8 1c                                 map, 28 pairs
18 54  1b 00 00 01 9f a6 84 d4 6a     key 84  -> uint64 1785205150826
18 65  03                             key 101 -> 3
18 53  39 01 df                       key 83  -> int -480
```

Key 84 decodes to a millisecond Unix epoch matching the moment of capture, key
83 to exactly UTC-8, and key 101 to 3, matching both the `facebd81` version
byte and the `version:3` identity field. Three independent cross-checks, so the
CBOR reading is not in doubt.

Status notifications on the same characteristic are small maps of the same
shape, appearing to be change-pushes rather than full snapshots:

```
a2 18 54 1b ... 02 f5     map(2) {84: <timestamp>, 2: true}
```

### 3.1 Property map

Observed in a live read. Note the read was cut short by the transport: the
device declared 28 pairs and 22 arrived, so keys beyond `preset_3` are not yet
seen.

| Key | Name | Type | Observed | Status |
|---|---|---|---|---|
| 1 | brightness | uint | 0-100 | [CONFIRMED] writable |
| 2 | power | bool | true/false | [CONFIRMED] |
| 3 | unknown | bool | false | [UNKNOWN] |
| 5 | durations | array | `[0, 0, 1800, 1800]` | [DERIVED] |
| 8 | colour | bytes(4) | RGBY | [UNKNOWN] stores but never renders |
| 19 | mode | bytes(3) | 5 values seen | [DERIVED] mode selector |
| 22 | **segments** | bytes(80) | 20 segments x RGBY | [CONFIRMED] writable |
| 23 | segment_brightness | bytes(20) | all 100 | [DERIVED] |
| 24 | dynamic_program | bytes(100) | header + 20 x RGBY | [DERIVED] |
| 41-43 | preset_1..3 | bytes(112) | header + 20 x RGBY | [DERIVED] |
| 66 | unknown | uint | 0 | [UNKNOWN] |
| 77 | schedule_times | array | `[0, 21600, 64800]` | [DERIVED] |
| 80 | unknown | array | `[0, 0]` | [UNKNOWN] |
| 83 | timezone_offset | int | -480 | [CONFIRMED] |
| 84 | timestamp | uint | ms epoch | [CONFIRMED] |
| 86-89 | unknown | bool/uint | 0 / false | [UNKNOWN] |
| 101 | version | uint | 3 | [CONFIRMED] |

`schedule_times` decodes as seconds into the day: 21600 = 06:00, 64800 = 18:00.
`durations` as seconds: 1800 = a 30 minute ramp.

### 3.2 The lamp is 20 addressable segments [CONFIRMED]

This is the single most important fact about the device, and it invalidates the
obvious first reading of the data.

Key 22 is 80 bytes. Read as a time series that is "20 animation steps of RGBY",
which is what it looks like in isolation. It is actually **spatial**: 20
physical segments, one 4-byte RGBY colour each.

Painting `R,G,B` repeating from the **top** reads back as:

```
array:     G  R  B  G  R  B  G  R  B  G  R  B  G  R  B  G  R  B  G  R
physical:  ^ bottom                                            top ^
```

The one-step offset is only explicable if index 0 is the **bottom** segment:

```
array[i] == physical_from_top[19 - i]
```

Verified by writing a deliberately asymmetric pattern (5 red, 5 green, 5 blue,
5 yellow from the top) and confirming visually that it rendered in that order.
Had the ordering been backwards, the bands would have appeared mirrored.

`glowctl`'s public helpers therefore default to **top-to-bottom** ordering and
reverse internally, because bottom-first is the opposite of how anyone
describes a standing lamp and silently mirrors every pattern when got wrong.

This also re-explains the factory preset (key 41), which is `R,G,B,Y` repeated
five times: a static rainbow down the lamp, not the four-step animation it
appears to be under the time-series reading. Likewise key 22's earlier
cool-to-warm ramp is a gradient along the lamp's length, not a sunrise fade.

Colour entries are four bytes, `R G B Y`. **Three-channel RGB is wrong.** The
reference "bright white" is `(255, 255, 255, 200)`, holding yellow back
rather than driving all four to full.

### 3.3 Commands [CONFIRMED]

Writing a CBOR property map to `facebd01` sets those properties. `facebd01`
advertises plain `write`, so it expects a write **with response**, and the
device acknowledges at the ATT layer.

| Action | Frame |
|---|---|
| power on | `a1 02 f5` |
| power off | `a1 02 f4` |
| brightness 75 | `a1 01 18 4b` |
| paint segments | `a1 16 58 50 <80 bytes>` |

All verified end to end: acknowledged, pushed a matching notification, read
back, and physically observed.

A write naming one key changes only that key, so partial updates are safe.
Notifications are change-pushes carrying the changed key plus a timestamp, not
full snapshots.

**Painting segments needs no mode change.** A per-segment write rendered
immediately while key 19 held constant at `00 00 05`.

### 3.4 Key 8 stores a colour that never renders [CONFIRMED]

Key 8 is 4 bytes and looks exactly like the display colour. It is not.

Writes to it round-trip byte-exact, which is why it looked correct for a whole
debugging cycle. But the lamp never changed. Conclusively: when
solid red was set, key 8 did not move **and did not contain red**. The
rendered colour was in key 22, with all 20 segments set to red.

What key 8 is for remains unidentified. It is marked unknown and not writable.

### 3.5 Key 19 reports the mode but does not control it [CONFIRMED]

Key 19 tracks the active mode faithfully. It changed when the physical mode
switch was pressed and differs across every distinct lighting state observed:

| Value | State |
|---|---|
| `01 00 04` | white |
| `01 00 00` | colour cycling |
| `00 00 06` | solid colour |
| `00 01 00` | per-segment pattern |
| `00 00 05` | all white |
| `02 00 07` | music reactive, "energy core" |
| `01 00 87` | seen once, unidentified |

**But writing it does nothing.** With the lamp music-reactive, writing
`00 00 05` (all white) was acknowledged, change-pushed, and read back. The lamp
reported all-white afterwards and carried on pulsing to sound.

So key 19 is a status record of the mode, not a direct control
surface. Writing it alone only desynchronises the report from
reality.

All three bytes vary and no structural reading has survived contact with the
next capture, so values are recorded raw in `const.MODE_VALUES` with no theory
attached.

**This is the central open problem.** Nothing found so far starts or stops a
mode. Real mode control must live in the six properties that never arrive in
the truncated read, or in a command form we have not observed.

### 3.7 An acknowledged write is not proof of effect

Two properties now store perfectly and drive nothing: key 8 and key 19. Both
looked correct from the protocol trace alone.

Every property this project marks writable, key 1, key 2 and key 22, was
confirmed **visually** on hardware. That standard exists because this device
will happily accept, acknowledge, echo and persist a write that changes nothing
at all.

### 3.6 Dynamic modes run on-device [CONFIRMED]

Two state reads four seconds apart while the lamp was actively colour cycling
differed **only** in the timestamp. Key 22 did not move.

So animation runs entirely on the device and is not reported over BLE. During
any dynamic mode, key 22 holds the configured pattern rather than what is
currently lit, and a UI must not present it as live output.

---

## 4. Open questions

1. **Which mode value renders the manual colour?** Key 19 byte 3 is the
   selector (3.5), but the two values mapped so far are white and cycling, and
   neither displays key 8. Set a solid colour and read state
   to capture the value that does.
2. **The rest of the key 19 mode enum**, including what `0x87` was.
4. **The remaining 6 properties** past the truncated read. The device declares
   28 pairs and 22 arrive. Re-read with a larger MTU, or read in blobs.
3. **Header layouts** for the program properties (keys 24, 41-43). Key 24's
   header begins `03 14 64 64 0a 02 03 00 01`, where `14` = 20 matches the step
   count and `64` = 100 a brightness.
5. **Scene IDs.** The protocol defines 15 scene names; none has been tied to a
   numeric value or a property key yet.
6. **Keys 3, 66, 80, 86-89** remain unidentified.
7. Whether `facebd03` is really OTA, and what guards it. Given the service has
   no authentication at all, this is worth knowing.

---

## 5. Method notes

### macOS TCC blocks CoreBluetooth from CLI tools

Any CLI process launched from a terminal or non-GUI script context is SIGABRT-killed on
its first CoreBluetooth call, because TCC attributes the request to the
**responsible process** (the parent app), not the binary. Embedding an
`NSBluetoothAlwaysUsageDescription` via a Mach-O `__TEXT,__info_plist` section
is **not sufficient** for this reason.

Running the Python CLI from Terminal.app or iTerm2 is unaffected: the terminal emulator
is itself an app bundle and macOS prompts for Bluetooth access normally.

### Captures

Verbatim captures are kept in `tests/data/` and used directly as test fixtures,
so the decoder is pinned to hardware behaviour rather than to anyone's reading
of it.

---

## 6. Modes and the DyData animation program [CONFIRMED]

A mode is **not** an index you send. It is two properties written together in
one frame:

    ModeCtr (key 19)   3 bytes, identifies the mode
    DyData  (key 24)   100 bytes, the animation program

Established by writing campfire's `DyData` at a lamp showing torch: it changed
but sat half-applied, with the middle of the column flickering and the top
stuck on its old colour. Adding campfire's `ModeCtr` completed it. `ModeCtr`
written alone does nothing at all.

The lamp has 62 built-in modes (see `tests/data/c045_modes.txt`), but their
programs are not published anywhere. Each must be captured once from the device
while the mode is active, after which it replays offline forever.

### 6.1 DyData layout

    bytes 0-19    header
    bytes 20-99   20 RGBY palette entries, BOTTOM-first like Section0

Palette ordering was confirmed behaviourally: with LED count 7 and red in
entries 0-6, exactly seven LEDs animated at the bottom of the column.

### 6.2 Header bytes

Orthogonal, safe to set independently within a given template:

| Byte | Meaning | How it was established |
|---|---|---|
| 1 | LED count, segments the effect spans | set 7, exactly 7 animated |
| 2 | bits 0-6 **101 - speed**, bit 7 **speed mode** | 100 to 1, 23 to 78, 50 to 51; uniform set bit 7 |
| 3 | brightness | 100 in every capture |
| 4 | total segments (20) | constant |
| 5 | response method, 0 stacking / 1 water drops | isolated cleanly |
| 8 | colour mode, see COLOUR_MODES | isolated cleanly |
| 18 | background brightness 0-100 | set 5, read 5 |

The background **colour** is `palette[19]`, the last slot, not a segment.

Speed and speed mode sharing one byte matters for any encoder: writing either
must preserve the other. `build_dydata` does, and a regression test covers it.

Owned by the dynamic type, rewritten when it changes:

| Byte | Meaning |
|---|---|
| 0 | dynamic type |
| 6 | direction/variant, **relabelled per type** |
| 7 | tail length 0-3, cleared by types that lack it |
| 9, 10 | 19 and 100 under descent |
| 11 | 250 under expandable, 0 otherwise, across all captures |
| 19 | moves with the type |

Still unmapped: **byte 12 only**. Campfire reads 5 and torch 12, both dynamic
type 3, while every custom capture reads 0, so it is a per-mode parameter
within Expandable. Bytes 13-17 are zero in all sixteen captures.

Speed being stored inverted, as a delay rather than a rate, explains why
campfire (1) and torch (3) both sit at near-maximum speed, which suits a
flicker and a flame.

### 6.3 Dynamic types

| Value | Type |
|---|---|
| 0 | descent |
| 1 | **unused or unknown** |
| 2 | gradient |
| 3 | expandable |
| 4 | random |
| 5 | marquee |

All five observed directly. The gap at 1 is real: gradient, expandable, random
and marquee came out consecutive at 2-5, from which this project predicted
descent = 1 and was wrong. Descent is 0.

Campfire and torch are both type 3, so built-in presets are built on
Expandable with no privileged path.

### 6.4 Why synthesis needs a template

Changing dynamic type rewrites six other header bytes, apparently
clearing parameters that do not apply to the new type. Byte 6's meaning also
shifts per type: "upward" produced 1 under one type, "downward expansion"
produced 1 under another.

So a header cannot be assembled from parameters alone without producing
invalid combinations. `build_dydata` therefore requires a captured
template and exposes only the orthogonal fields plus the palette.

### 6.5 Dynamic type selection requires parameter updates

Selecting a dynamic type on hardware updates its parameter table.
Updating any value afterwards flushes it. A capture taken between those two
actions shows the previous type while the parameter map shows the new one.

---

## 7. The truncated read, and the way around it [CONFIRMED]

A read of `facebd02` returns exactly **738 bytes**, every time, while the CBOR
header declares 28 pairs and only 22 arrive.

### 7.1 Cause

The negotiated ATT_MTU is **247**, so a read fetches 246 bytes per PDU and
738 = 3 x 246 exactly. The device serves three full PDUs and stops. Its
attribute is capped at 738 bytes in firmware regardless of how much its
encoder produced. Why 738 specifically is unknown.

**Measure the MTU after service discovery.** Querying
`maximumWriteValueLength(.withoutResponse)` inside `didConnect` returns 20,
implying MTU 23, because the exchange has not completed. This project once
recorded that and built a whole explanation on it; notifications of 128 bytes,
impossible at MTU 23, exposed the error.

This is not fixable from the central. A central cannot force a larger MTU, and
single reads hit the same MTU limit.

### 7.2 The notification echo

**Writing a property makes the device push a notification carrying it back**,
including properties a read never returns. Writing
`{79: [1, 23400, 72900]}` produced:

```
{84: <timestamp>, 79: [1, 23400, 72900], 65: 06 01 11 28}
```

Key 79 echoed verbatim and key 65 arrived unprompted. This is the only
verification channel for the unreadable properties, and it is how RiseSet was
confirmed.

The echo is **not universal**. Six of the eight unreadable keys respond:
4, 40, 62, 63, 65, 79. Two do not: `baseAd` (7) and `extra` (125) accept writes
silently, so their content has never been observed by any means.

Note the echo reflects what you sent. It proves storage, not effect, and it
cannot disclose a value you did not already know.

---

## 8. Timers [CONFIRMED]

Five slots, `GTimeDat0..4` at keys 4, 40, 41, 42, 43. Each is **12 bytes of
config followed by a 100-byte DyData program**.

Timer slots are filled in order, so a single timer lands in slot 0, which
is past the read truncation. Three timers are needed before one appears in a
readable slot. That is why several earlier experiments found no trace of a
timer anywhere.

### Config layout

| Byte | Meaning |
|---|---|
| 0 | bit 7 enable, **bits 0-6 weekday bitmask** |
| 1 | hour |
| 2 | minute |
| 3 | action, 1 = on, 0 = off |
| 5 | unexplained; moved 100 to 101 with an action change |
| 10 | `0x80` whenever the slot is in use |
| 4, 6-9, 11 | identical in every slot observed |

"Once" is `0x80` (enabled, no day bits) and "daily" is `0xff` (enabled, all
seven), so byte 0 packs the enable bit together with a weekday bitmask.

Verified end to end by building a timer in Python (06:53, weekdays `0x1f`,
action on), writing it, and reading it back byte for byte.

---

## 9. Sunrise and sunset [CONFIRMED]

Three properties, none of them readable:

| Key | Name | Size | Content |
|---|---|---|---|
| 79 | `RiseSet` | 12 | `[enabled, rise_seconds, set_seconds]` |
| 63 | `RCfg` | 102 | sunrise scene: 2 B config + 100 B program |
| 62 | `SCfg` | 102 | sunset scene, same layout |
| 65 | `RStime` | 4 | device-computed; accompanies every write in this family |

`RiseSet` was confirmed by echo. `RCfg`/`SCfg` accept writes built as 2 + 100.

**The device stores more than it declares.** Writing 102 bytes to `SCfg`
echoed back **106**, with `40 40 00 7e` appended. Identical for both. So a
declared size is the writable portion, not necessarily what the firmware keeps.

`RStime` is the one unreadable property whose genuine content has been seen,
because the device volunteers it. It reads `06 01 11 28`, plausibly the
hour/minute pairs 06:01 and 17:40, unexplained.
