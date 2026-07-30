# Troubleshooting, FAQ & Home Automation Guide

This guide details platform Bluetooth configuration, device discovery troubleshooting, frequently asked questions, and Home Assistant integration patterns for `glowctl`.

---

## 1. Platform Bluetooth Setup & Permissions

### Linux (BlueZ)

On Linux systems using BlueZ, executing `glowctl` without `sudo` requires granting capabilities to the Python executable:

```bash
# Grant raw BLE socket access to your Python binary
sudo setcap 'cap_net_raw,cap_net_admin+eip' $(readlink -f $(which python3))
```

Alternatively, add your user to the `bluetooth` group:
```bash
sudo usermod -aG bluetooth $USER
```

### macOS (CoreBluetooth)

On macOS, BLE access is managed by Transparency, Consent, and Control (TCC):
* Launching `glowctl` from terminal emulators (Terminal.app, iTerm2, or VS Code terminal) prompts a one-time macOS system prompt: *"Terminal would like to use Bluetooth"*.
* Ensure Bluetooth is enabled under **System Settings > Privacy & Security > Bluetooth**.

### Windows (WinRT)

Windows uses the native WinRT Bluetooth stack:
* Ensure Bluetooth is turned ON in Windows Settings.
* No driver installation or administrator privileges are required for standard BLE GATT access.

---

## 2. Hardware Discovery & Connection Troubleshooting

### Device Only Advertises When Disconnected

The Glowrium H4 lamp advertises over BLE **only when no active central connection exists**.

* **Symptom**: `glowctl scan` or commands return `Device not found`.
* **Resolution**: Ensure the official mobile app or any secondary BLE central (such as a smartphone) is closed or disconnected. Once disconnected, the lamp immediately resumes BLE advertising.

### BLE Address Caching & Fast Mode

To eliminate scanning delays (saving ~3–5 seconds per command):
* `glowctl` caches the last successfully connected BLE address in `~/.cache/glowctl/last_device`.
* Use the `--fast` flag for sub-500ms fire-and-forget execution:
  ```bash
  glowctl on --fast
  glowctl set-color red --fast
  ```

---

## 3. Home Assistant Integration

Integrate `glowctl` into [Home Assistant](https://www.home-assistant.io/) to control your Glowrium floor lamp via automations, scripts, or dashboard buttons.

### Method 1: Shell Command Integration

Add the following to your Home Assistant `configuration.yaml`:

```yaml
shell_command:
  glow_lamp_on: "glowctl on --fast"
  glow_lamp_off: "glowctl off --fast"
  glow_lamp_brightness: "glowctl set-brightness {{ brightness_pct }} --fast"
  glow_lamp_red: "glowctl set-color red --fast"
  glow_lamp_blue: "glowctl set-color blue --fast"
  glow_lamp_mode_campfire: "glowctl set-mode campfire --fast"
```

Call these commands inside Home Assistant automations or dashboard scripts:

```yaml
action: shell_command.glow_lamp_on
```

### Method 2: Systemd Background Service (Linux Server)

To run `glowctl` on a dedicated Linux home server:

Create `/etc/systemd/system/glowctl-night.service`:

```ini
[Unit]
Description=Glowrium Lamp Night Mode
After=bluetooth.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/glowctl set-mode night_light --fast

[Install]
WantedBy=multi-user.target
```

Enable and trigger the service:
```bash
sudo systemctl daemon-reload
sudo systemctl start glowctl-night.service
```

---

## 4. Frequently Asked Questions (FAQ)

### Q1: Can I control the lamp locally over Wi-Fi?
**No.** Port scans confirm the lamp exposes zero listening TCP/UDP ports on local Wi-Fi. It connects outbound to cloud MQTT servers (`iot.ledinpro.com`). Local control is performed exclusively over Bluetooth Low Energy (BLE).

### Q2: Why does state reading return 22 properties instead of 28?
Firmware caps GATT read responses on `facebd02` at exactly **738 bytes** (3 ATT PDUs of 246 bytes). Unreadable properties past key 43 are acknowledged via notification echoes upon writing.

### Q3: How are spatial segments indexed?
Hardware memory stores segment 0 at the physical bottom of the column. `glowctl` translates all API and CLI segment indices top-to-bottom (index 0 = top segment) for intuitive control.

### Q4: Does `glowctl` support multi-segment RGBY gradients?
**Yes.** `glowctl` provides 4-channel per-segment control (Red, Green, Blue, Yellow). You can set individual colors across all 20 segments via CLI or Python API:
```python
from glowctl import GlowClient

async with GlowClient() as client:
    # Paint top segment red, bottom segment blue
    colors = [(255, 0, 0, 0)] + [(0, 0, 0, 0)] * 18 + [(0, 0, 255, 0)]
    await client.set_segments(colors)
```
