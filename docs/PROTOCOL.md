# Glowrium C045 Protocol Specification

This specification defines the hardware control protocol, GATT service layout, and CBOR property maps for Glowrium (INLEDCO) LED floor lamps.

---

## 1. Device Identification

| Property | Value |
|---|---|
| Product Name | Glowrium H4 Smart LED Corner Floor Lamp |
| Model Designation | Glowrium-C045 |
| Light Output | 1440 Lumens |
| Power Rating | 36 Watts |
| Illumination Angle | 160° Wide-Angle Glow |
| Color Temp Range | 2000K (Warm Yellow) to 7500K (Daylight) |
| WiFi MAC | `88:56:A6:F2:36:4C` |
| BLE MAC | `88:56:A6:F2:36:4E` |
| MAC OUI | `88:56:A6` (Espressif Inc.) |
| LAN Address | `192.168.0.81` (DHCP) |
| BLE Advertised Name | `Glowrium-H4_F2364E` |
| SoC Family | ESP32 (classic, dual-mode) |
| Protocol Version | 3 |

The device transmits identity attributes as a semicolon-delimited string on characteristic `facebd80`:

```text
brand:GLOWRIUM;pkey:Glowrium-C045;subid:1;devid:ESP-8856A6F2364C;mac:8856A6F2364C;version:3;;
```

### Advertising Payload

Service data frame under `FACEBD00`:

```text
00 65 | 88 56 a6 f2 36 4e | 03 00 2d 01
       \_____ BLE MAC ____/
```

The payload contains the BLE MAC address and the protocol version indicator (`03`).

---

## 2. Transport & Communication Architecture

### 2.1 Local Control Surface

The device operates as a BLE peripheral and an outbound MQTT client. It exposes no listening TCP/UDP server ports on local Wi-Fi. Local control is executed exclusively via Bluetooth Low Energy (BLE).

### 2.2 Cloud Transport Parameters

| Parameter | Endpoint / Value |
|---|---|
| REST API Endpoint | `http://iot.ledinpro.com:8080/api/` |
| Alternate REST API Endpoint | `http://47.251.4.156:9008/api/` |
| MQTT Provisioning | Dynamic per-account runtime provisioning (`device/mqtt_param`) |

### 2.3 BLE GATT Layout

Primary GATT Service: `facebd00-7261-6262-6974-696f74626c65` (ASCII suffix `rabbitiotble`). This GATT service layout is shared across INLEDCO hardware models (C011, C017, C037, C041, C045, C063, C066, C086, C094, 469).

| Characteristic | GATT Properties | Functional Role |
|---|---|---|
| `facebd01` | `write` | Command write channel |
| `facebd02` | `read`, `write`, `notify` | Property map state & status notifications |
| `facebd03` | `write`, `notify` | Firmware transfer / OTA channel |
| `facebd80` | `read`, `write`, `notify` | Device identity payload |
| `facebd81` | `read` | Protocol version byte (`0x03`) |

* **Authentication**: GATT connection requires no pairing or bonding. Unbonded central devices can connect, read state maps, and subscribe to notifications immediately.
* **Advertising State**: The lamp advertises over BLE only when no central device is connected.

---

## 3. CBOR Wire Protocol Specification

The protocol exchanges Concise Binary Object Representation (CBOR) property maps keyed by unsigned integer property IDs.

Reading `facebd02` returns a CBOR map header (`b8 1c`, declaring 28 key-value pairs):

```text
b8 1c                                 map, 28 pairs
18 54  1b 00 00 01 9f a6 84 d4 6a     key 84  -> uint64 (Unix timestamp ms)
18 65  03                             key 101 -> uint 3 (protocol version)
18 53  39 01 df                       key 83  -> int -480 (timezone offset minutes)
```

Push notifications on `facebd02` transmit delta updates containing changed keys:

```text
a2 18 54 1b ... 02 f5                 map(2) {84: <timestamp>, 2: true}
```

### 3.1 Property Map Reference

| Key | Name | Type | Description |
|---|---|---|---|
| 1 | `brightness` | uint | Global luminance level (0–100) |
| 2 | `power` | bool | Power state (`true` = On, `false` = Off) |
| 3 | `unknown_3` | bool | Internal boolean flag |
| 5 | `durations` | array | Countdown and ramp durations (`[0, 0, 1800, 1800]`) |
| 8 | `colour` | bytes(4) | Stored color register (RGBY) |
| 19 | `mode` | bytes(3) | Active mode identifier (`ModeCtr`) |
| 22 | **`segments`** | bytes(80) | 20 spatial segments x 4-channel RGBY |
| 23 | `segment_brightness` | bytes(20) | Per-segment brightness override array |
| 24 | `dynamic_program` | bytes(100) | Active dynamic animation program (`DyData`) |
| 41–43 | `preset_1..3` | bytes(112) | Alarm timer program slots |
| 66 | `unknown_66` | uint | Configuration feature status counter |
| 77 | `schedule_times` | array | Hourly chime window limits (`[enabled, start_sec, end_sec]`) |
| 80 | `gradual_fade` | array | Power-on/off fade configuration (`[enabled, duration_sec]`) |
| 83 | `timezone_offset` | int | Timezone offset in minutes (e.g. -480 for UTC-8) |
| 84 | `timestamp` | uint | Millisecond Unix epoch timestamp |
| 86–89 | `unknown_86..89` | bool/uint | System configuration attributes |
| 101 | `version` | uint | Firmware protocol version (`3`) |

---

### 3.2 Spatial Segment Array (20 Addressable Segments)

Key 22 contains an 80-byte binary payload structured as **20 spatial segments**, with each segment represented by 4 bytes: `[Red, Green, Blue, Yellow]`.

* **4-Channel RGBY Emitters**: Each segment features dedicated Red, Green, Blue, and Yellow LED emitters. Pure white luminance is driven by `(255, 255, 255, 200)` to preserve dedicated yellow emitter balance.
* **Physical Memory Mapping**: Segment data is stored bottom-first in physical memory. Array index `0` corresponds to the physical bottom segment of the column:

$$\text{array}[i] = \text{physical\_from\_top}[19 - i]$$

High-level library helpers accept top-to-bottom segment arrays and execute internal byte reversing to match user mental models.

---

### 3.3 Command Frames

Command frames written to characteristic `facebd01` require acknowledged GATT write operations:

| Action | CBOR Frame |
|---|---|
| Power On | `a1 02 f5` |
| Power Off | `a1 02 f4` |
| Set Brightness (75%) | `a1 01 18 4b` |
| Paint 20 Segments | `a1 16 58 50 <80 bytes>` |

---

### 3.4 Lighting Mode & State Control

The active lighting mode is identified by `ModeCtr` (Key 19, 3 bytes). To execute dynamic animations, `ModeCtr` (Key 19) and `DyData` (Key 24, 100 bytes) must be written together in a single property payload:

| Key 19 Value | Lighting Mode State |
|---|---|
| `01 00 04` | Solid White |
| `01 00 00` | Color Cycling |
| `00 00 06` | Solid Color |
| `00 01 00` | Per-Segment Custom Pattern |
| `00 00 05` | High-Luminance White |
| `02 00 07` | Sound/Music Reactive ("Energy Core") |

---

## 4. Dynamic Animation Programs (`DyData`)

Dynamic mode animation parameters are encoded in `DyData` (Key 24), a 100-byte structure consisting of a **20-byte header** and an **80-byte palette array** (20 steps of 4-channel RGBY colors).

```text
bytes 0-19:   Header parameters
bytes 20-99:  20-step RGBY palette array (bottom-first order)
```

### 4.1 Header Parameter Map

| Byte | Parameter | Range / Meaning |
|---|---|---|
| 0 | Dynamic Type | Animation algorithm selector (0–5) |
| 1 | Segment Count | Active segment span (1–20) |
| 2 | Speed & Speed Mode | Bits 0–6: inverted speed delay ($101 - \text{speed}$), Bit 7: motion mode (`0` step, `1` smooth) |
| 3 | Brightness | Mode luminance level (0–100) |
| 4 | Segment Total | Total segment count (`20`) |
| 5 | Response Method | Stacking (`0`) or drop animation (`1`) |
| 6 | Direction / Variant | Movement direction (`0` downward, `1` upward) |
| 7 | Tail Length | Animation trail length (0–3) |
| 8 | Color Mode | Palette iteration mode (cycle, random, fixed) |
| 18 | Background Brightness | Luminance of inactive segments outside `segment_count` (0–100) |

The background color register is defined by `palette[19]` (the final palette slot).

### 4.2 Dynamic Types

| Value | Type Identifier |
|---|---|
| 0 | Descent |
| 2 | Gradient |
| 3 | Expandable |
| 4 | Random |
| 5 | Marquee |

Because dynamic types rewrite type-specific header parameters, custom animation synthesis via `build_dydata()` modifies parameters over a captured base template profile.

---

## 5. ATT MTU Truncation & Notification Echo Behavior

### 5.1 Response Truncation Dynamics

State reads on characteristic `facebd02` return a maximum payload of **738 bytes**. Although the CBOR property map header declares 28 key-value pairs, only 22 pairs are returned in a standard read payload.

* **MTU Allocation**: Under standard BLE negotiation (ATT_MTU = 247), each GATT read PDU yields up to 246 payload bytes. The firmware limits multi-PDU read responses to 3 PDUs ($3 \times 246 = 738$ bytes).
* **Timing & Service Discovery**: ATT_MTU negotiation completes after GATT service discovery. Measuring payload limits prematurely inside `didConnect` yields pre-negotiation defaults (23 bytes).
* **Firmware Limit**: The 738-byte boundary is enforced in device firmware and cannot be expanded by central MTU renegotiation.

---

### 5.2 Notification Echo Channel

Property writes transmitted to `facebd01` trigger an automatic notification echo on `facebd02`, containing the written property key and timestamp payload:

```json
{84: <timestamp>, 79: [1, 23400, 72900], 65: "06 01 11 28"}
```

* **State Verification**: For unreadable properties located past the 738-byte read truncation boundary (such as keys 4, 40, 62, 63, 65, and 79), the notification echo acts as the primary hardware verification channel.
* **Echo Behavior**:
  - **Echo-Supported Keys**: Keys `4`, `40`, `62`, `63`, `65`, and `79` echo property updates upon writing.
  - **Silent Keys**: Keys `7` (`baseAd`) and `125` (`extra`) acknowledge GATT writes without emitting notification echoes.

---

## 6. Schedules & Hardware Timers

### 6.1 Alarm Timer Slots (`GTimeDat0..4`)

The hardware provides 5 alarm timer slots at keys `4`, `40`, `41`, `42`, and `43`. Each slot payload contains a **12-byte configuration header** followed by a **100-byte `DyData` animation program**.

| Byte | Parameter | Range / Encoding |
|---|---|---|
| 0 | Enable & Weekday Mask | Bit 7: Enable flag, Bits 0–6: Weekday mask (`0x7F` = daily, `0x00` = once) |
| 1 | Hour | Alarm hour (0–23) |
| 2 | Minute | Alarm minute (0–59) |
| 3 | Action | Power action (`1` = On, `0` = Off) |
| 10 | Active Flag | `0x80` when slot is active |

---

### 6.2 Sunrise & Sunset Schedule

Scheduled sunrise and sunset triggers are configured across four property attributes:

| Key | Attribute Name | Format / Payload |
|---|---|---|
| 79 | `RiseSet` | Array `[enabled, rise_seconds, set_seconds]` |
| 63 | `RCfg` | Sunrise scene configuration (2B header + 100B `DyData`) |
| 62 | `SCfg` | Sunset scene configuration (2B header + 100B `DyData`) |
| 65 | `RStime` | Device-calculated schedule timestamp payload |

---

## 7. Operating System Environment Notes

On macOS, CoreBluetooth access requires appropriate process permissions attributed to the host terminal emulator (Terminal.app or iTerm2). Launching CLI tools within an active terminal session inherits the terminal application's Bluetooth TCC permissions.
