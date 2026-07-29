"""BLE transport for Glowrium lamps.

This layer handles scanning, connecting, discovering the
device service, subscribing to notifications, and writing frames.

That split is deliberate, you can use `probe()` to explore a real device and
capture its notification traffic before any command encoding exists.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from . import const

log = logging.getLogger(__name__)


@dataclass
class DiscoveredLamp:
    """A Glowrium seen while scanning."""

    name: str
    address: str
    rssi: int
    device: BLEDevice = field(repr=False)

    @property
    def suffix(self) -> str:
        """The MAC-derived suffix from the advertising name, e.g. 'f2364E'."""
        return self.name.rsplit("_", 1)[-1] if "_" in self.name else ""


async def discover(timeout: float = 10.0,
                   name_prefix: str = const.NAME_PREFIX,
                   stop_on_first: bool = False) -> list[DiscoveredLamp]:
    """Scan for advertising Glowrium lamps.

    Args:
        timeout: how long to scan at most.
        stop_on_first: return as soon as one lamp is seen. The lamp usually
            advertises within a second or two, so waiting out the full timeout
            is dead time for any command that just needs *a* lamp. Leave this
            False when enumerating everything in range.

    Note the lamp only advertises while unconnected. If this returns nothing,
    ensure no other device or client is connected and power-cycle the lamp
    before concluding it is unreachable.
    """
    found: dict[str, DiscoveredLamp] = {}
    seen = asyncio.Event()

    def callback(device: BLEDevice, adv) -> None:
        name = adv.local_name or device.name or ""
        if name_prefix.lower() in name.lower():
            found[device.address] = DiscoveredLamp(
                name=name, address=device.address, rssi=adv.rssi, device=device
            )
            seen.set()

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        if stop_on_first:
            try:
                await asyncio.wait_for(seen.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(timeout)
    finally:
        await scanner.stop()

    return sorted(found.values(), key=lambda d: -d.rssi)


class LampTransport:
    """A connected BLE session with a lamp.

    Usage:
        async with LampTransport(device) as t:
            await t.write(frame)
    """

    def __init__(self, device: BLEDevice | str,
                 notify_callback: Callable[[str, bytes], None] | None = None):
        self._target = device
        self._client: BleakClient | None = None
        self._notify_callback = notify_callback
        self._chars: dict[str, object] = {}

    async def __aenter__(self) -> "LampTransport":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def connect(self, timeout: float = 20.0) -> None:
        self._client = BleakClient(self._target, timeout=timeout)
        await self._client.connect()
        log.info("connected to %s", self._target)
        await self._discover_chars()
        await self._subscribe_all()

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def _discover_chars(self) -> None:
        """Locate the device service's characteristics by short UUID key."""
        assert self._client
        for service in self._client.services:
            if service.uuid.lower() != const.SERVICE_UUID.lower():
                continue
            for char in service.characteristics:
                short = char.uuid.lower()[:8]
                if short in const.CHAR_UUIDS:
                    self._chars[short] = char
                    log.debug("found %s props=%s", char.uuid, char.properties)

        if not self._chars:
            raise RuntimeError(
                f"device service {const.SERVICE_UUID} not found on this device; "
                f"it may not be a Glowrium, or may expose a different firmware"
            )

    async def _subscribe_all(self) -> None:
        """Subscribe to every notifiable characteristic.

        The device protocol expects an explicit notification subscription step
        before commands are acknowledged. We subscribe to all candidates.
        """
        assert self._client
        for short, char in self._chars.items():
            props = getattr(char, "properties", [])
            if "notify" not in props and "indicate" not in props:
                continue
            try:
                await self._client.start_notify(char, self._on_notify)
                log.info("subscribed to %s", short)
            except Exception as exc:  # noqa: BLE001 - report and continue
                log.warning("could not subscribe to %s: %s", short, exc)

    def _on_notify(self, char, data: bytearray) -> None:
        payload = bytes(data)
        short = str(getattr(char, "uuid", char)).lower()[:8]
        log.debug("notify %s: %s", short, payload.hex(" "))
        if self._notify_callback:
            self._notify_callback(short, payload)

    async def write(self, frame: bytes, char: str = "facebd01",
                    response: bool = True) -> None:
        """Write a raw frame to a characteristic.

        Args:
            frame: raw bytes to send.
            char: short UUID key. facebd01 is the write-only command channel.
            response: write WITH response. Defaults True because facebd01
                advertises plain `write` and not `writeWithoutResponse`; sending
                without a response is silently dropped by the device, so every
                command appears to succeed while nothing happens.
        """
        if not self.is_connected:
            raise RuntimeError("not connected")
        target = self._chars.get(char)
        if target is None:
            raise KeyError(
                f"characteristic {char} not present; available: "
                f"{sorted(self._chars)}"
            )
        await self._client.write_gatt_char(target, frame, response=response)
        log.debug("wrote %s to %s", frame.hex(" "), char)

    async def read(self, char: str) -> bytes:
        if not self.is_connected:
            raise RuntimeError("not connected")
        target = self._chars.get(char)
        if target is None:
            raise KeyError(f"characteristic {char} not present")
        return bytes(await self._client.read_gatt_char(target))

    async def read_state(self) -> dict[int, object]:
        """Read and decode the full device state property map."""
        from . import protocol
        return protocol.decode_state(await self.read("facebd02"))

    async def read_identity(self) -> dict[str, str]:
        """Read and parse the device identity string."""
        from . import protocol
        return protocol.parse_identity(await self.read("facebd80"))

    async def read_version(self) -> int:
        """Read the protocol version byte."""
        data = await self.read("facebd81")
        return data[0] if data else -1

    async def send_properties(self, props: dict[int, object], *,
                              allow_unsafe: bool = False) -> bytes:
        """Encode and write a property map to the command characteristic.

        Returns the frame that was sent, which is useful for logging what a
        given high-level action actually put on the wire.
        """
        from . import protocol
        frame = protocol.encode_properties(props, allow_unsafe=allow_unsafe)
        await self.write(frame, char="facebd01")
        return frame

    def describe(self) -> list[dict]:
        """Report each discovered characteristic and its properties."""
        out = []
        for short, char in sorted(self._chars.items()):
            out.append({
                "short": short,
                "uuid": str(getattr(char, "uuid", "")),
                "properties": list(getattr(char, "properties", [])),
            })
        return out
