"""CLI timing benchmarks and --fast mode verification tests."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from glowctl import cli, const, protocol


@pytest.fixture
def mock_transport():
    with patch("glowctl.cli.LampTransport") as mock_cls, \
         patch("glowctl.cli._resolve", new_callable=AsyncMock) as mock_resolve:
        mock_instance = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_instance
        mock_resolve.return_value = "AA:BB:CC:DD:EE:FF"
        yield mock_instance, mock_resolve


def test_fast_mode_power_on(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x02\xf5"

    start = time.perf_counter()
    res = cli.main(["--fast", "on"])
    elapsed = time.perf_counter() - start

    assert res == 0
    assert elapsed < 0.1  # Fast mode must complete in under 100ms when mocked
    transport.send_properties.assert_called_once_with({const.KEY_POWER: True}, allow_unsafe=False)


def test_fast_mode_power_off(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x02\xf4"

    start = time.perf_counter()
    res = cli.main(["--fast", "off"])
    elapsed = time.perf_counter() - start

    assert res == 0
    assert elapsed < 0.1
    transport.send_properties.assert_called_once_with({const.KEY_POWER: False}, allow_unsafe=False)


def test_fast_mode_brightness(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x01\x18\x50"

    start = time.perf_counter()
    res = cli.main(["--fast", "brightness", "80"])
    elapsed = time.perf_counter() - start

    assert res == 0
    assert elapsed < 0.1
    transport.send_properties.assert_called_once_with({const.KEY_BRIGHTNESS: 80}, allow_unsafe=False)


def test_fast_mode_color(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x16..."

    start = time.perf_counter()
    res = cli.main(["--fast", "color", "255", "0", "0"])
    elapsed = time.perf_counter() - start

    assert res == 0
    assert elapsed < 0.1
    expected_segments = protocol.encode_solid(255, 0, 0, 0)
    transport.send_properties.assert_called_once_with({
        const.KEY_MODE: const.MODE_VALUES["solid"],
        const.KEY_SEGMENTS: expected_segments,
    }, allow_unsafe=False)


def test_fast_mode_color_with_yellow(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x16..."

    start = time.perf_counter()
    res = cli.main(["--fast", "color", "0", "255", "0", "128"])
    elapsed = time.perf_counter() - start

    assert res == 0
    assert elapsed < 0.1
    expected_segments = protocol.encode_solid(0, 255, 0, 128)
    transport.send_properties.assert_called_once_with({
        const.KEY_MODE: const.MODE_VALUES["solid"],
        const.KEY_SEGMENTS: expected_segments,
    }, allow_unsafe=False)


def test_fast_mode_bypasses_readback_sleep(mock_transport):
    transport, _ = mock_transport
    transport.send_properties.return_value = b"\xa1\x02\xf5"

    # Fast mode should NOT call read_state
    cli.main(["--fast", "on"])
    transport.read_state.assert_not_called()
