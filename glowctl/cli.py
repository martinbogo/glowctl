"""Command line interface for glowctl.

    glowctl scan                    find advertising lamps
    glowctl info                    identity string, version, GATT layout
    glowctl state                   read and decode the full property map
    glowctl watch                   stream status notifications
    glowctl on / off                power
    glowctl brightness 75           brightness, 0-100
    glowctl segments                show each of the 20 segments
    glowctl color 255 0 0           paint every segment one RGBY colour
    glowctl chime on --start 07:00 --end 22:00
    glowctl gradual on --seconds 25
    glowctl countdown 600           countdown in seconds, 0 disables
    glowctl sunrise on --rise 06:00 --set 21:00
    glowctl timers                  show the five timer slots
    glowctl timer 2 07:30 --repeat daily --action on
    glowctl mode campfire           apply a captured mode
    glowctl mode list               show captured modes
    glowctl capture-mode aurora     harvest whatever the lamp shows now
    glowctl raw 'a1 02 f5'          send a raw frame you built yourself

The lamp is a strip of 20 addressable segments, so "colour" means painting all
of them. Each control command prints the exact bytes it sends and reads the
state back, so a wrong inference shows up immediately rather than silently.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import const, modes as modes_registry, protocol
from .transport import LampTransport, discover


def _render(key: int, value) -> str:
    """Human-readable form of a property value.

    Raw bytes repr is useless here: an 80-byte segment map prints as ASCII
    mojibake because 0x32 happens to be the character '2'.
    """
    if value is None:
        return "(not returned by this read)"
    if not isinstance(value, bytes):
        return repr(value)
    if key == const.KEY_SEGMENTS and len(value) == 80:
        segs = protocol.parse_segments(value)
        uniq = set(segs)
        if len(uniq) == 1:
            r, g, b, y = segs[0]
            return f"all 20 segments R={r} G={g} B={b} Y={y}"
        return f"20 segments, {len(uniq)} distinct, top={segs[-1]} bottom={segs[0]}"
    if key == const.KEY_DYDATA and len(value) == 100:
        i = protocol.parse_dydata(value)
        return (f"type={i['dynamic_type']} led={i['led_count']} "
                f"speed={i['speed']} tail={i['tail_length']}")
    if len(value) <= 12:
        return value.hex(" ")
    return f"{len(value)} bytes: {value[:8].hex(' ')} ..."


def _log_setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _cache_file() -> Path:
    cache_dir = Path.home() / ".cache" / "glowctl"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "last_device"


def _get_cached_address() -> str | None:
    try:
        cf = _cache_file()
        if cf.exists():
            addr = cf.read_text().strip()
            if addr:
                return addr
    except Exception:
        pass
    return None


def _save_cached_address(address: str) -> None:
    try:
        cf = _cache_file()
        cf.write_text(address + "\n")
    except Exception:
        pass


async def _resolve(args):
    """Return something connectable, using cached address or scanning if needed."""
    if getattr(args, "address", None):
        _save_cached_address(args.address)
        return args.address

    if not getattr(args, "scan_first", False):
        cached = _get_cached_address()
        if cached:
            if getattr(args, "debug", False):
                print(f"using cached device address {cached}")
            return cached

    lamps = await discover(timeout=args.timeout, stop_on_first=True)
    if not lamps:
        print("No lamp found.\n", file=sys.stderr)
        print("The lamp only advertises while nothing is connected to it.",
              file=sys.stderr)
        print("Ensure no other client is connected, power-cycle the lamp, and retry.",
              file=sys.stderr)
        return None
    _save_cached_address(lamps[0].address)
    if getattr(args, "debug", False):
        print(f"using {lamps[0].name} ({lamps[0].address}, rssi {lamps[0].rssi})")
    return lamps[0].device


async def cmd_scan(args) -> int:
    print(f"scanning {args.timeout:.0f}s for '{const.NAME_PREFIX}*' ...")
    lamps = await discover(timeout=args.timeout)
    if not lamps:
        print("\nNo lamps found. The lamp only advertises while nothing is")
        print("connected to it, so ensure no other client is connected and power-cycle it.")
        return 1
    for lamp in lamps:
        print(f"  {lamp.name:<28} {lamp.address}  rssi={lamp.rssi}")
    return 0


async def cmd_info(args) -> int:
    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        identity = await t.read_identity()
        print("\n=== identity ===")
        for k, v in identity.items():
            print(f"  {k:<10} {v}")
        try:
            print(f"  {'protocol':<10} {await t.read_version()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  protocol   (unreadable: {exc})")

        print("\n=== characteristics ===")
        for c in t.describe():
            print(f"  {c['short']}  {', '.join(c['properties']) or 'none'}")
    return 0


async def cmd_state(args) -> int:
    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        state = await t.read_state()
        print(f"\n=== device state, {len(state)} properties ===")
        print("  key  name                 confidence  value")
        for line in protocol.describe_state(state):
            print(line)
        if args.programs:
            _print_programs(state)
    return 0


def _print_programs(state: dict) -> None:
    """Expand the per-segment RGBY payloads, shown top to bottom."""
    layouts = {22: 0, 24: 20, 41: 32, 42: 32, 43: 32}
    for key, header in layouts.items():
        value = state.get(key)
        if not isinstance(value, bytes):
            continue
        prop = const.PROPERTIES.get(key)
        name = prop.name if prop else f"key_{key}"
        print(f"\n=== {name} (key {key}), {header}B header, top to bottom ===")
        for i, (r, g, b, y) in enumerate(protocol.segments_from_top(value, header)):
            print(f"  segment {i:2}: R={r:3} G={g:3} B={b:3} Y={y:3}")


async def cmd_segments(args) -> int:
    """Show what each of the lamp's 20 segments is currently set to."""
    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        state = await t.read_state()
        raw = state.get(const.KEY_SEGMENTS)
        if not isinstance(raw, bytes):
            print("segment data not present in this read", file=sys.stderr)
            return 1
        print(f"\n=== {const.SEGMENT_COUNT} segments, top to bottom ===")
        for i, (r, g, b, y) in enumerate(protocol.segments_from_top(raw)):
            bar = "#" * (max(r, g, b, y) * 20 // 255)
            print(f"  {i:2}  R={r:3} G={g:3} B={b:3} Y={y:3}  {bar}")
        print("\n(the device stores these bottom-first; shown here top-first)")
    return 0


async def cmd_watch(args) -> int:
    target = await _resolve(args)
    if target is None:
        return 1

    def on_notify(short: str, data: bytes) -> None:
        try:
            decoded = protocol.decode_state(data)
            pretty = ", ".join(
                f"{const.PROPERTIES[k].name if k in const.PROPERTIES else k}={v!r}"
                for k, v in sorted(decoded.items())
            )
        except Exception:  # noqa: BLE001 - fall back to hex on anything odd
            pretty = data.hex(" ")
        print(f"  {short}  {pretty}")

    async with LampTransport(target, notify_callback=on_notify) as t:
        print(f"\n=== watching {args.listen:.0f}s ===")
        await asyncio.sleep(args.listen)
    return 0


async def _write_and_verify(t: LampTransport, props: dict[int, object], *,
                           args, expect_readback: bool = True) -> int:
    try:
        frame = await t.send_properties(props, allow_unsafe=args.unsafe)
    except protocol.UnsafeProperty as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "debug", False):
        summary = ", ".join(
            f"{const.PROPERTIES[k].name if k in const.PROPERTIES else k}"
            f"={_render(k, v)}" for k, v in props.items())
        print(f"sent {len(frame)}B: {summary}")

    if getattr(args, "fast", False):
        return 0

    state = {}
    delays = (0.6, 1.0, 1.4) if getattr(args, "slow_verify", False) else (0.1, 0.25, 0.5)
    for delay in delays:
        await asyncio.sleep(delay)
        state = await t.read_state()
        if all(k in const.NEVER_READ or state.get(k) == v
               for k, v in props.items()):
            break

    failed = False
    for key, sent in props.items():
        prop = const.PROPERTIES.get(key)
        name = prop.name if prop else f"key_{key}"
        if key in const.NEVER_READ and not expect_readback:
            if getattr(args, "debug", False):
                print(f"  {name}: not returned by a read (verified by echo)")
            continue
        got = state.get(key)
        if got != sent:
            print(f"  {name}: {_render(key, got)}   [MISMATCH, "
                  f"expected {_render(key, sent)}]", file=sys.stderr)
            failed = True
        elif getattr(args, "debug", False):
            print(f"  {name}: {_render(key, got)}   [ok]")
    return 1 if failed else 0


async def _apply(args, props: dict[int, object], *,
                 expect_readback: bool = True) -> int:
    """Send a property map, then read state back so the effect is visible."""
    target = await _resolve(args)
    if target is None:
        return 1

    try:
        async with LampTransport(target) as t:
            return await _write_and_verify(t, props, args=args, expect_readback=expect_readback)
    except Exception as exc:
        cached = _get_cached_address()
        if cached and not getattr(args, "address", None) and not getattr(args, "scan_first", False):
            if getattr(args, "debug", False):
                print(f"connection to cached address {cached} failed ({exc}); rescanning...")
            lamps = await discover(timeout=args.timeout, stop_on_first=True)
            if lamps:
                _save_cached_address(lamps[0].address)
                async with LampTransport(lamps[0].device) as t:
                    return await _write_and_verify(t, props, args=args, expect_readback=expect_readback)
        raise


async def cmd_power(args) -> int:
    return await _apply(args, {const.KEY_POWER: args.on})


async def cmd_brightness(args) -> int:
    level = int(args.level)
    if not 0 <= level <= 100:
        print("brightness must be 0-100", file=sys.stderr)
        return 2
    return await _apply(args, {const.KEY_BRIGHTNESS: level})


async def cmd_color(args) -> int:
    if len(args.values) == 1 and args.values[0].lower() in const.NAMED_COLORS:
        name = args.values[0].lower()
        segment_colors = const.NAMED_COLORS[name]
        payload = protocol.encode_segments(segment_colors)
        target_mode = const.MODE_VALUES["segments"] if name == "aurora" else const.MODE_VALUES["solid"]
    else:
        try:
            vals = [int(v) for v in args.values]
        except ValueError:
            valid_names = ", ".join(sorted(const.NAMED_COLORS))
            print(f"invalid color: {args.values[0]!r}. Use numeric RGBY values (0-255) or named color: {valid_names}", file=sys.stderr)
            return 2
        while len(vals) < const.CHANNEL_COUNT:
            vals.append(0)
        if any(not 0 <= v <= 255 for v in vals):
            print("each channel must be 0-255", file=sys.stderr)
            return 2
        payload = protocol.encode_solid(*vals[:const.CHANNEL_COUNT])
        target_mode = const.MODE_VALUES["solid"]

    props = {}
    if not args.keep_mode:
        props[const.KEY_MODE] = target_mode
    props[const.KEY_SEGMENTS] = payload
    return await _apply(args, props)


def _hhmm(text: str) -> int:
    """Parse HH:MM into seconds since midnight."""
    try:
        h, _, m = text.partition(":")
        h, m = int(h), int(m or 0)
    except ValueError:
        raise SystemExit(f"expected HH:MM, got {text!r}")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise SystemExit(f"time out of range: {text}")
    return h * 3600 + m * 60


async def cmd_chime(args) -> int:
    on = args.state == "on"
    return await _apply(args, {const.KEY_CHIME: [
        int(on), _hhmm(args.start), _hhmm(args.end)]})


async def cmd_gradual(args) -> int:
    on = args.state == "on"
    return await _apply(args, {const.KEY_GRADUAL: [int(on), int(args.seconds)]})


async def cmd_countdown(args) -> int:
    secs = int(args.seconds)
    on = secs > 0
    # Element 3 is the device's live remaining time; start it at the full total.
    return await _apply(args, {const.KEY_COUNTDOWN: [int(on), 0, secs, secs]})


async def cmd_sunrise(args) -> int:
    """Sunrise/sunset schedule.

    Key 79 is never returned by a state read, so _apply's readback will show
    nothing. The device does echo the write as a notification, which is how
    this property was verified in the first place.
    """
    on = args.state == "on"
    return await _apply(args, {const.KEY_RISESET:
                               [int(on), _hhmm(args.rise), _hhmm(args.set)]},
                        expect_readback=False)


TIMER_SLOT_KEYS = {0: 4, 1: 40, 2: 41, 3: 42, 4: 43}


async def cmd_timers(args) -> int:
    """Show the timer slots a state read can reach."""
    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        state = await t.read_state()
        print(f"\n=== timer slots ===")
        for slot, key in sorted(TIMER_SLOT_KEYS.items()):
            raw = state.get(key)
            if not isinstance(raw, bytes):
                print(f"  slot {slot} (key {key:>2}): not returned by a read "
                      f"(past the read truncation; the cut-off is set by the host\n                      Bluetooth stack and differs by platform)")
                continue
            p = protocol.parse_timer_slot(raw)
            state_str = "enabled" if p["enabled"] else "disabled"
            print(f"  slot {slot} (key {key:>2}): {state_str:<9} {p['time']} "
                  f"{p['repeat']:<12} action={p['action']}")
        print("\nSlots 0 and 1 are filled first by initial configuration and cannot "
              "be read;\nwrite them and the device echoes the value back.")
    return 0


async def cmd_timer(args) -> int:
    """Write a timer into a slot, using a readable slot as the template."""
    if args.slot not in TIMER_SLOT_KEYS:
        print(f"slot must be 0-4, got {args.slot}", file=sys.stderr)
        return 2
    key = TIMER_SLOT_KEYS[args.slot]
    hour, minute = divmod(_hhmm(args.time) // 60, 60)

    days = protocol.TIMER_DAILY if args.repeat == "daily" else 0
    if args.days is not None:
        days = int(args.days, 0)

    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        state = await t.read_state()
        template = next((state[k] for k in (41, 42, 43)
                         if isinstance(state.get(k), bytes)), None)
        if template is None:
            print("no readable timer slot to use as a template", file=sys.stderr)
            return 1
        slot = protocol.build_timer_slot(
            template, hour=hour, minute=minute, enabled=not args.disable,
            days=days, action=args.action)
        p = protocol.parse_timer_slot(slot)
        print(f"slot {args.slot}: {p['time']} {p['repeat']} action={p['action']} "
              f"enabled={p['enabled']}")
        await t.send_properties({key: slot}, allow_unsafe=True)
        print(f"written to key {key}")
    return 0


async def cmd_mode(args) -> int:
    if args.name == "list":
        names = modes_registry.available()
        if not names:
            print("no modes captured yet; see 'glowctl capture-mode'")
            return 1
        for n in names:
            print(f"  {n:<14} {modes_registry.describe(n)}")
        return 0
    try:
        props = modes_registry.get(args.name)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2
    return await _apply(args, props)


async def cmd_capture_mode(args) -> int:
    """Harvest the mode the lamp is currently showing, so it can be replayed."""
    target = await _resolve(args)
    if target is None:
        return 1
    async with LampTransport(target) as t:
        state = await t.read_state()
        modectr, dydata = state.get(const.KEY_MODE), state.get(const.KEY_DYDATA)
        if not isinstance(modectr, bytes) or not isinstance(dydata, bytes):
            print("could not read ModeCtr/DyData from this device", file=sys.stderr)
            return 1
        modes_registry.save(args.name, modectr, dydata,
                            index=args.index, display=args.display)
        info = protocol.parse_dydata(dydata)
        print(f"captured {args.name!r}: ModeCtr {modectr.hex()}, "
              f"palette of {info['palette_length']} colours")
        print(f"replay it with: glowctl mode {args.name}")
    return 0


async def cmd_raw(args) -> int:
    try:
        frame = bytes.fromhex(args.hex.replace(" ", "").replace(":", ""))
    except ValueError:
        print(f"not valid hex: {args.hex!r}", file=sys.stderr)
        return 2
    if not frame:
        print("empty frame", file=sys.stderr)
        return 2

    target = await _resolve(args)
    if target is None:
        return 1

    def on_notify(short: str, data: bytes) -> None:
        print(f"  NOTIFY {short} {data.hex(' ')}")

    async with LampTransport(target, notify_callback=on_notify) as t:
        print(f"writing {frame.hex(' ')} to {args.char}")
        await t.write(frame, char=args.char, response=args.response)
        await asyncio.sleep(args.listen)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parent_p = argparse.ArgumentParser(add_help=False)
    parent_p.add_argument("-d", "--debug", action="store_true", default=argparse.SUPPRESS,
                          help="show the lamp found, the frame sent, and readbacks")
    parent_p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                          help="library logging as well as --debug output")
    parent_p.add_argument("--address", default=argparse.SUPPRESS, help="BLE address/UUID, skipping the scan")
    parent_p.add_argument("--scan", dest="scan_first", action="store_true", default=argparse.SUPPRESS,
                          help="force BLE scan instead of using cached device address")
    parent_p.add_argument("--fast", "--no-wait", action="store_true", default=argparse.SUPPRESS,
                          help="fire-and-forget mode; return immediately after write ACK")
    parent_p.add_argument("--slow-verify", action="store_true", default=argparse.SUPPRESS,
                          help="use conservative 2-3s readback verification delays")
    parent_p.add_argument("--timeout", type=float, default=10.0,
                          help="scan timeout in seconds (default 10)")
    parent_p.add_argument("--unsafe", action="store_true", default=argparse.SUPPRESS,
                          help="permit writing properties whose meaning is unconfirmed")

    p = argparse.ArgumentParser(
        prog="glowctl",
        description="Open source control for Glowrium LED lamps.",
        parents=[parent_p],
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", parents=[parent_p], help="find advertising Glowrium lamps")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("info", parents=[parent_p], help="identity, version, and GATT layout")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("state", parents=[parent_p], help="read and decode the property map")
    s.add_argument("--programs", action="store_true",
                   help="also expand the RGBY step programs")
    s.set_defaults(func=cmd_state)

    s = sub.add_parser("segments", parents=[parent_p], help="show each segment's colour")
    s.set_defaults(func=cmd_segments)

    s = sub.add_parser("watch", parents=[parent_p], help="stream status notifications")
    s.add_argument("--listen", type=float, default=30.0)
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("on", parents=[parent_p], help="turn the lamp on")
    s.set_defaults(func=cmd_power, on=True)
    s = sub.add_parser("off", parents=[parent_p], help="turn the lamp off")
    s.set_defaults(func=cmd_power, on=False)

    s = sub.add_parser("brightness", parents=[parent_p], help="set brightness 0-100")
    s.add_argument("level")
    s.set_defaults(func=cmd_brightness)

    s = sub.add_parser("color", parents=[parent_p], help="set RGBY channels, 0-255 each")
    s.add_argument("values", nargs="+", metavar="N",
                   help="red green blue [yellow]")
    s.add_argument("--keep-mode", action="store_true",
                   help="do not leave animation mode; a running effect will "
                        "overwrite the colour")
    s.set_defaults(func=cmd_color)

    s = sub.add_parser("chime", parents=[parent_p], help="hourly chime on/off and its active window")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--start", default="06:00", metavar="HH:MM")
    s.add_argument("--end", default="18:00", metavar="HH:MM")
    s.set_defaults(func=cmd_chime)

    s = sub.add_parser("gradual", parents=[parent_p], help="fade duration for on/off transitions")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--seconds", type=int, default=10)
    s.set_defaults(func=cmd_gradual)

    s = sub.add_parser("countdown", parents=[parent_p], help="countdown timer in seconds (0 disables)")
    s.add_argument("seconds", type=int)
    s.set_defaults(func=cmd_countdown)

    s = sub.add_parser("sunrise", parents=[parent_p], help="sunrise/sunset schedule")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--rise", default="06:00", metavar="HH:MM")
    s.add_argument("--set", dest="set", default="18:00", metavar="HH:MM")
    s.set_defaults(func=cmd_sunrise)

    s = sub.add_parser("timers", parents=[parent_p], help="show the timer slots")
    s.set_defaults(func=cmd_timers)

    s = sub.add_parser("timer", parents=[parent_p], help="write a timer into a slot")
    s.add_argument("slot", type=int, help="0-4; slots 0 and 1 cannot be read back")
    s.add_argument("time", metavar="HH:MM")
    s.add_argument("--action", choices=["on", "off"], default="on")
    s.add_argument("--repeat", choices=["once", "daily"], default="once")
    s.add_argument("--days", help="explicit weekday bitmask, e.g. 0x1f for Mon-Fri")
    s.add_argument("--disable", action="store_true", help="clear the enable bit")
    s.set_defaults(func=cmd_timer)

    s = sub.add_parser("mode", parents=[parent_p], help="apply a captured mode ('list' to see them)")
    s.add_argument("name")
    s.set_defaults(func=cmd_mode)

    s = sub.add_parser("capture-mode", parents=[parent_p],
                       help="save the mode the lamp is showing right now")
    s.add_argument("name")
    s.add_argument("--index", type=int, default=None)
    s.add_argument("--display", default=None)
    s.set_defaults(func=cmd_capture_mode)

    s = sub.add_parser("raw", parents=[parent_p], help="send a raw hex frame")
    s.add_argument("hex", help='e.g. "a1 02 f5"')
    s.add_argument("--char", default="facebd01")
    s.add_argument("--response", action="store_true")
    s.add_argument("--listen", type=float, default=3.0)
    s.set_defaults(func=cmd_raw)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for attr, default_val in [
        ("debug", False),
        ("verbose", False),
        ("fast", False),
        ("unsafe", False),
        ("timeout", 10.0),
        ("address", None),
        ("scan_first", False),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default_val)

    if args.verbose:
        args.debug = True
    _log_setup(args.verbose)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if "turned off" in str(exc).lower() or "bluetooth" in str(exc).lower():
            print(f"Bluetooth Error: {exc}", file=sys.stderr)
            print("Please ensure Bluetooth is enabled on this system and retry.", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
