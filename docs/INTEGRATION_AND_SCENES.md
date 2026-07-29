# Integration & Scene Building Guide

This guide covers how to integrate `glowctl` as a Python library, configure device settings, and build custom animation scenes using parameter synthesis.

---

## Table of Contents

1. [Integrating `glowctl` as a Library](#1-integrating-glowctl-as-a-library)
   - [Installation & Imports](#installation--imports)
   - [Device Discovery](#device-discovery)
   - [Connecting & Transport Management](#connecting--transport-management)
   - [Reading & Parsing Device State](#reading--parsing-device-state)
   - [Streaming GATT Notifications](#streaming-gatt-notifications)
   - [Error Handling & Safety Guards](#error-handling--safety-guards)
2. [Configuring Device Settings](#2-configuring-device-settings)
   - [Power & Brightness](#power--brightness)
   - [Spatial RGBY Colors](#spatial-rgby-colors)
   - [Hourly Chime](#hourly-chime)
   - [Gradual Fade Transitions](#gradual-fade-transitions)
   - [Countdown Timer](#countdown-timer)
   - [Sunrise & Sunset Schedule](#sunrise--sunset-schedule)
   - [Alarm Timer Slots](#alarm-timer-slots)
3. [Building Custom Scenes & Dynamic Animations](#3-building-custom-scenes--dynamic-animations)
   - [Mode Architecture (`ModeCtr` + `DyData`)](#mode-architecture-modectr--dydata)
   - [Synthesizing Programs (`build_dydata`)](#synthesizing-programs-build_dydata)
   - [Parameter Reference Table](#parameter-reference-table)
   - [Complete Custom Scene Example](#complete-custom-scene-example)

---

## 1. Integrating `glowctl` as a Library

`glowctl` provides an `asyncio`-based Python API built on top of `bleak` and `cbor2`.

### Installation & Imports

```python
import asyncio
from glowctl import (
    DiscoveredLamp,
    LampTransport,
    discover,
    modes,
    protocol,
    const,
)
```

### Device Discovery

Use `discover()` to scan for advertising Glowrium lamps:

```python
# Scan for up to 10 seconds and return as soon as one lamp is found
lamps = await discover(timeout=10.0, stop_on_first=True)
if not lamps:
    print("No lamp found.")
    return

lamp_info: DiscoveredLamp = lamps[0]
print(f"Found {lamp_info.name} at {lamp_info.address} (RSSI: {lamp_info.rssi})")
```

### Connecting & Transport Management

Manage connection lifecycle using `LampTransport` as an async context manager:

```python
async with LampTransport(lamp_info.device) as lamp:
    # Check connection status
    print(f"Connected: {lamp.is_connected}")

    # Send power-on command
    await lamp.write(protocol.encode_power(True))
```

If you already know the BLE MAC address or UUID, pass it directly:

```python
async with LampTransport("AA:BB:CC:DD:EE:FF") as lamp:
    await lamp.write(protocol.encode_brightness(80))
```

### Reading & Parsing Device State

Read the complete state map or specific characteristics:

```python
async with LampTransport(lamp_info.device) as lamp:
    # Read and decode CBOR property map
    state = await lamp.read_state()

    # Print annotated property summary
    for line in protocol.describe_state(state):
        print(line)

    # Parse 20 spatial segments (top to bottom)
    raw_segments = state.get(const.KEY_SEGMENTS)
    if isinstance(raw_segments, bytes):
        segments = protocol.segments_from_top(raw_segments)
        for idx, (r, g, b, y) in enumerate(segments):
            print(f"Segment {idx:2d}: R={r} G={g} B={b} Y={y}")
```

### Streaming GATT Notifications

Register a callback to process live status notifications push-event updates:

```python
def handle_notification(char_short: str, payload: bytes):
    try:
        decoded = protocol.decode_state(payload)
        print(f"Notification from {char_short}: {decoded}")
    except Exception:
        print(f"Raw notification from {char_short}: {payload.hex()}")

async with LampTransport(lamp_info.device, notify_callback=handle_notification) as lamp:
    # Stream notifications for 30 seconds
    await asyncio.sleep(30.0)
```

### Error Handling & Safety Guards

Writing unrecognized CBOR property keys is guarded by `UnsafeProperty` exceptions to prevent accidental firmware misconfiguration:

```python
try:
    # Attempting to write an unconfirmed property key will raise UnsafeProperty
    await lamp.send_properties({125: b"\x00" * 10})
except protocol.UnsafeProperty as exc:
    print(f"Safety guard blocked write: {exc}")

# To explicitly override safety guards for experimental probing:
await lamp.send_properties({125: b"\x00" * 10}, allow_unsafe=True)
```

---

## 2. Configuring Device Settings

Every setting helper returns raw CBOR bytes ready to send via `lamp.write()`.

### Power & Brightness

```python
# Power controls
await lamp.write(protocol.encode_power(True))   # On
await lamp.write(protocol.encode_power(False))  # Off

# Global luminance (0 to 100)
await lamp.write(protocol.encode_brightness(75))
```

### Spatial RGBY Colors

The column contains 20 addressable segments. `encode_segment_colours()` accepts 20 RGBY tuples ordered top-to-bottom:

```python
# Solid color across all 20 segments (RGB + Yellow channel)
await lamp.write(protocol.encode_colour(255, 0, 0, 0))        # Red
await lamp.write(protocol.encode_colour(0, 255, 0, 100))      # Green + Warm Yellow

# Top-to-bottom spatial color gradient
gradient = [
    (int(255 * (1 - i / 19)), int(255 * (i / 19)), 0, 50)
    for i in range(20)
]
await lamp.write(protocol.encode_segment_colours(gradient, from_top=True))
```

### Hourly Chime

Program hourly chime notification windows:

```python
# Enable chime between 07:00 (25200s) and 22:00 (79200s)
await lamp.write(protocol.encode_chime(
    enabled=True,
    start_seconds=7 * 3600,
    end_seconds=22 * 3600,
))
```

### Gradual Fade Transitions

Configure smooth luminance fade durations for power on/off transitions:

```python
# Set 15-second gradual fade transition
await lamp.write(protocol.encode_gradual(enabled=True, duration_seconds=15))
```

### Countdown Timer

Program hardware countdown timer:

```python
# Start 10-minute countdown (600 seconds)
await lamp.write(protocol.encode_countdown(enabled=True, seconds=600))

# Disable countdown
await lamp.write(protocol.encode_countdown(enabled=False, seconds=0))
```

### Sunrise & Sunset Schedule

Program scheduled sunrise and sunset scene triggers:

```python
# Sunrise at 06:30 (23400s), Sunset at 21:00 (75600s)
await lamp.write(protocol.encode_sunrise_sunset(
    enabled=True,
    rise_seconds=6 * 3600 + 30 * 60,
    set_seconds=21 * 3600,
))
```

### Alarm Timer Slots

The hardware supports 5 timer slots (`GTimeDat0` .. `GTimeDat4`). Edit slot configurations using a captured slot template:

```python
# Read current device state to get a template slot
state = await lamp.read_state()
template_slot = state.get(const.PROPERTIES[41].key)  # Slot 2 (key 41)

# Build a daily timer for 07:30 AM turning the lamp ON
new_slot = protocol.build_timer_slot(
    template_slot,
    hour=7,
    minute=30,
    enabled=True,
    days=protocol.TIMER_DAILY,  # Mon-Sun mask (0x7F)
    action="on",
)

# Write to Slot 2 (key 41)
await lamp.send_properties({41: new_slot}, allow_unsafe=True)
```

---

## 3. Building Custom Scenes & Dynamic Animations

### Mode Architecture (`ModeCtr` + `DyData`)

Lighting modes are defined by two properties written together:
1. `ModeCtr` (key 19): 3-byte mode identifier.
2. `DyData` (key 24): 100-byte program consisting of a **20-byte header** and a **20-step RGBY palette array**.

```python
frame = protocol.encode_mode(modectr_bytes, dydata_bytes)
await lamp.write(frame)
```

### Synthesizing Programs (`build_dydata`)

Because `DyData` headers contain dynamic-type-specific parameters, custom scenes are synthesized by overriding parameters of a real captured mode template:

```python
# Load a captured template
template_dydata = modes.get("custom_descent")[24]

# Synthesize new program parameters
custom_program = protocol.build_dydata(
    template_dydata,
    palette=[(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0)],
    led_count=10,
    speed=40,
    brightness=100,
    direction=1,
    tail_length=2,
)
```

### Parameter Reference Table

| Parameter | Type / Range | Description |
|---|---|---|
| `template` | `bytes` (100B) | **Required**. Base captured `DyData` program. |
| `palette` | `list[tuple]` | List of up to 20 `(R, G, B, Y)` tuples (0-255 each). |
| `palette_length` | `int` (1-20) | Number of active palette colors the device cycles through. |
| `led_count` | `int` (1-20) | Number of vertical segments the animation spans. |
| `speed` | `int` (0-100) | Animation speed scale (stored inverted in header). |
| `speed_mode` | `int` (`0` / `1`) | `0`: Accelerating step, `1`: Uniform motion. |
| `brightness` | `int` (0-100) | Mode luminance level. |
| `direction` | `int` (`0` / `1`) | Animation flow direction (e.g. downward vs upward). |
| `tail_length` | `int` (0-3) | Animation trail length / decay steps. |
| `colour_mode` | `int` | Palette iteration mode (`cycle_rotation`, `random`, `first`). |
| `background_brightness` | `int` (0-100) | Brightness of unlit segments outside `led_count`. |

---

### Complete Custom Scene Example

The following script synthesizes a custom multi-colored wave effect over a captured template and applies it to the hardware:

```python
import asyncio
from glowctl import LampTransport, build_dydata, discover, modes, protocol

async def main():
    # 1. Discover lamp
    lamps = await discover(stop_on_first=True)
    if not lamps:
        print("No lamp found.")
        return

    # 2. Retrieve base template from captured profiles
    mode_profile = modes.get("custom_descent")
    modectr = mode_profile[19]
    template_dydata = mode_profile[24]

    # 3. Build custom animation program
    # Define a 5-color palette (Red, Amber, Green, Cyan, Blue)
    custom_palette = [
        (255, 0, 0, 0),      # Red
        (255, 128, 0, 50),   # Amber + Yellow
        (0, 255, 0, 0),      # Green
        (0, 255, 255, 0),    # Cyan
        (0, 0, 255, 0),      # Blue
    ]

    custom_dydata = build_dydata(
        template_dydata,
        palette=custom_palette,
        palette_length=len(custom_palette),
        led_count=15,
        speed=60,
        brightness=100,
        direction=1,
        tail_length=3,
        background_brightness=10,
    )

    # 4. Encode and transmit frame
    frame = protocol.encode_mode(modectr, custom_dydata)

    async with LampTransport(lamps[0].device) as lamp:
        await lamp.write(frame)
        print("Custom scene transmitted successfully.")

if __name__ == "__main__":
    asyncio.run(main())
```
