"""Captured lighting modes.

A mode on this device is not an index you send. It is a pair of properties
written together:

    ModeCtr (key 19)   3 bytes, identifies the mode
    DyData  (key 24)   100 bytes, the animation program itself

Confirmed on hardware. Writing DyData alone changes the lamp but leaves it in a
half-applied state; writing ModeCtr alone does nothing at all. Written
together, they apply the mode parameters completely.

Modes are defined by program maps and parameters. Each mode can be captured using
`glowctl capture-mode <name>`. Captured modes are stored here and can be replayed
offline.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "modes.json"


def _load() -> dict:
    if not _DATA.exists():
        return {}
    return json.loads(_DATA.read_text())


def available() -> list[str]:
    """Names of every mode captured so far."""
    return sorted(_load())


def get(name: str) -> dict:
    """Return a captured mode's raw properties, keyed by CBOR key."""
    modes = _load()
    if name not in modes:
        raise KeyError(
            f"mode {name!r} has not been captured. Available: "
            f"{', '.join(sorted(modes)) or '(none)'}. "
            f"Capture it from a live device using: glowctl capture-mode {name}"
        )
    m = modes[name]
    return {19: bytes.fromhex(m["modectr"]), 24: bytes.fromhex(m["dydata"])}


def describe(name: str) -> str:
    m = _load()[name]
    dy = bytes.fromhex(m["dydata"])
    return (f"{m.get('display', name)} (index {m.get('index', '?')}), "
            f"palette of {dy[4]} colours, speed byte {dy[1]}")


def save(name: str, modectr: bytes, dydata: bytes,
         index: int | None = None, display: str | None = None) -> None:
    """Record a mode harvested from a live device."""
    modes = _load()
    modes[name] = {
        "index": index,
        "display": display or name,
        "modectr": modectr.hex(),
        "dydata": dydata.hex(),
    }
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(modes, indent=2, sort_keys=True) + "\n")
