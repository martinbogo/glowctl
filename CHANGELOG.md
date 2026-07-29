# Changelog & Release Notes

## v1.0.0 — Initial Release

`glowctl` is an open-source Python library and command-line interface for controlling **Glowrium** (INLEDCO) LED floor lamps over Bluetooth Low Energy (BLE).

---

### Highlights & Key Features

#### 1. Hardware Architecture & Spatial RGBY Control
- **Target Device**: Full hardware-verified support for **Glowrium H4 Smart LED Corner Floor Lamp** (Model `Glowrium-C045` / ESP32 SoC).
- **Physical Specifications**: 1440 Lumens brightness, 36W power rating, 160° wide-angle illumination, 2000K–7500K color temperature range.
- **20 Addressable Segments**: Top-to-bottom spatial color rendering across all 20 vertical segments.
- **4-Channel RGBY Emitters**: Independent control of Red, Green, Blue, and Yellow emitters per segment.
- **CBOR-over-BLE Transport**: Encodes and decodes CBOR property maps on characteristic `facebd01` (write) and `facebd02` (read/notify).

#### 2. High-Performance CLI (`glowctl`)
- **Sub-500ms Execution (`--fast`)**: Fire-and-forget mode returns immediately after ATT write ACK.
- **Automatic BLE Address Caching**: Caches discovered lamp address in `~/.cache/glowctl/last_device` to bypass scan latency on subsequent runs.
- **Commands Available**:
  - `glowctl scan` / `info` / `state` / `segments` / `watch`
  - `glowctl on` / `off` / `brightness` / `color`
  - `glowctl chime` / `gradual` / `countdown` / `sunrise` / `timers` / `timer`
  - `glowctl mode list` / `mode <name>` / `capture-mode` / `raw`

#### 3. Configuration & Settings API
- **Hourly Chime**: Program notification windows (e.g. 07:00 to 22:00).
- **Gradual Fade Transitions**: Configure power transition luminance fade durations (e.g. 15s / 25s).
- **Countdown Timer**: Hardware countdown timer trigger (seconds resolution).
- **Sunrise & Sunset Schedule**: Schedule daily sunrise/sunset lighting triggers.
- **Alarm Timer Slots**: Manage five hardware alarm timer slots (`GTimeDat0` .. `GTimeDat4`).

#### 4. Dynamic Animation Synthesis (`build_dydata`)
- **Profile Capture & Replay**: Capture current device program state to named local profiles.
- **Parametric Scene Synthesis**: Override template mode parameters (`palette`, `led_count`, `speed`, `brightness`, `direction`, `tail_length`, `colour_mode`, `background_brightness`) to generate original animation frames.

#### 5. Embedded Python Library (`glowctl`)
- `LampTransport` async context manager for BLE GATT connections.
- `discover()` helper with `stop_on_first` fast discovery option.
- `decode_state()` and `describe_state()` for CBOR property map inspection.
- Live GATT push notification streaming callbacks (`notify_callback`).
- Safety guards (`UnsafeProperty`) protecting unconfirmed firmware parameters.

#### 6. Cross-Platform Support & Testing
- Tested across **Linux** (BlueZ), **Windows 10/11** (WinRT), and **macOS** (CoreBluetooth).
- **110 Hardware-Verified Unit Tests** running on Python 3.10, 3.11, and 3.12.
- **Automated GitHub Actions**: Wiki sync workflow (`wiki_sync.yml`), CI test matrix (`test.yml`), and release asset publisher (`release.yml`).
