# CLI Reference (`glowctl`)

Complete command-line interface reference guide for `glowctl`.

---

## Performance & Optimization Flags

* `--fast` / `--no-wait`: Return immediately after the ATT write ACK is received without waiting for state readbacks (**~0.3s–0.5s response time**).
* `--scan`: Force a fresh BLE scan instead of using the cached device address from `~/.cache/glowctl/last_device`.
* `--address <ADDR>`: Connect directly to a specific BLE MAC address or UUID.
* `--slow-verify`: Use conservative 2–3s readback verification delays.

---

## Discovery & Status Commands

```bash
glowctl scan                      # Discover advertising Glowrium lamps
glowctl info                      # Display device identity and GATT services
glowctl state                     # Read and decode full property map
glowctl state --programs          # Print decoded 20-segment RGBY values
glowctl segments                  # Visual breakdown of current segment colors
glowctl watch                     # Stream live GATT notifications
```

---

## Power & Lighting Control

```bash
glowctl on
glowctl off
glowctl --fast on                 # Fire-and-forget sub-500ms power on
glowctl brightness 75             # Set global luminance (0-100)
glowctl color 255 0 0             # Set all segments to RGB (Yellow default 0)
glowctl color 255 128 0 100       # Set explicit Red, Green, Blue, Yellow channels
glowctl color aurora              # Apply 20-segment Aurora Borealis spatial gradient
glowctl color red                 # Apply named color preset (red, green, blue, yellow, white, aurora)
```

---

## Schedules & Timers

```bash
glowctl gradual on --seconds 25   # Set power transition fade time
glowctl chime on --start 07:00 --end 22:00
glowctl countdown 600             # Start countdown timer in seconds (0 disables)
glowctl sunrise on --rise 06:00 --set 21:00
glowctl timers                    # View programmed timer slots
glowctl timer 2 07:30 --repeat daily --action on
```

---

## Mode Replay & Capture

```bash
glowctl mode list                 # List available captured mode profiles
glowctl mode campfire             # Replay a specific animation profile
glowctl capture-mode aurora       # Save active device program to a local profile
glowctl raw 'a1 02 f5'            # Write custom CBOR hex frame to command characteristic
```
