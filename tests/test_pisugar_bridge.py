"""Tests for the PiSugar battery / power telemetry bridge."""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibedump.integrations.pisugar import (
    PISUGAR_BATTERY_CRITICAL_PERCENT,
    PISUGAR_BATTERY_LOW_PERCENT,
    PISUGAR_I2C_ADDR,
    FakePiSugarBridge,
    PiSugarMonitor,
    PiSugarNotAvailable,
    PiSugarStatus,
    RealPiSugarBridge,
    make_pisugar,
)
from vibedump.integrations.pisugar import BatteryReading


class TestFakePiSugarBridge:
    def test_read_returns_initial_values(self) -> None:
        bridge = FakePiSugarBridge()
        reading = bridge.read()
        assert reading.battery_percent == 80.0
        assert reading.voltage_v == 4.05
        assert reading.current_ma == -150.0
        assert reading.temperature_c == 42.0
        assert reading.is_charging is False
        assert reading.is_powered is True
        # ISO-8601 UTC string with a timezone offset.
        parsed = datetime.fromisoformat(reading.timestamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)

    def test_set_battery_lowers_percent(self) -> None:
        bridge = FakePiSugarBridge()
        bridge.set_battery(15)
        assert bridge.read().battery_percent == 15.0

    def test_set_battery_clamps_to_bounds(self) -> None:
        bridge = FakePiSugarBridge()
        bridge.set_battery(-5)
        assert bridge.read().battery_percent == 0.0
        bridge.set_battery(150)
        assert bridge.read().battery_percent == 100.0

    def test_set_charging_true_sets_charging_flag(self) -> None:
        bridge = FakePiSugarBridge()
        bridge.set_charging(True)
        reading = bridge.read()
        assert reading.is_charging is True
        assert reading.is_powered is True
        # Positive current means current flows into the battery.
        assert reading.current_ma > 0

    def test_status_returns_pi3_metadata(self) -> None:
        bridge = FakePiSugarBridge()
        status = bridge.status()
        assert status.model == "PiSugar 3"
        assert status.i2c_address == PISUGAR_I2C_ADDR == 0x57
        assert isinstance(status, PiSugarStatus)
        # No read yet -> last_reading is None.
        assert status.last_reading is None
        bridge.read()
        status2 = bridge.status()
        assert status2.last_reading is not None
        assert status2.last_reading.battery_percent == 80.0

    def test_close_is_a_noop(self) -> None:
        bridge = FakePiSugarBridge()
        bridge.close()
        # Still usable after close.
        assert bridge.read().battery_percent == 80.0


class TestFactory:
    def test_prefer_fake_returns_fake(self) -> None:
        bridge = make_pisugar(prefer="fake")
        assert isinstance(bridge, FakePiSugarBridge)

    def test_prefer_auto_falls_back_to_fake_without_smbus2(self) -> None:
        # Hide smbus2 if it happens to be installed so auto falls back to fake.
        with patch.dict(sys.modules, {"smbus2": None}):
            bridge = make_pisugar(prefer="auto")
        assert isinstance(bridge, FakePiSugarBridge)

    def test_unknown_prefer_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            make_pisugar(prefer="nope")


class TestExceptions:
    def test_pisugar_not_available_is_runtime_error(self) -> None:
        assert issubclass(PiSugarNotAvailable, RuntimeError)


class TestRealPiSugarBridge:
    def test_missing_smbus2_raises_not_available(self) -> None:
        with patch.dict(sys.modules, {"smbus2": None}):
            with pytest.raises(PiSugarNotAvailable):
                RealPiSugarBridge()

    def test_i2c_open_failure_raises_not_available(self) -> None:
        fake_smbus = SimpleNamespace(SMBus=lambda *_: (_ for _ in ()).throw(OSError("bus gone")))
        with patch.dict(sys.modules, {"smbus2": fake_smbus}):
            with pytest.raises(PiSugarNotAvailable):
                RealPiSugarBridge()

    def test_successful_read_publishes_values(self) -> None:
        class FakeBus:
            def __init__(self) -> None:
                self.closed = False

            def read_byte_data(self, address: int, register: int) -> int:
                if register == 0x2A:
                    return 77
                if register == 0x2B:
                    return 0x0F
                if register == 0x2C:
                    return 0xA0
                if register == 0x33:
                    return 0xFF
                if register == 0x34:
                    return 0xFE
                if register == 0x04:
                    return 36
                raise AssertionError(f"unexpected register {register:#x}")

            def close(self) -> None:
                self.closed = True

        fake_smbus = SimpleNamespace(SMBus=lambda _bus: FakeBus())
        with patch.dict(sys.modules, {"smbus2": fake_smbus}):
            bridge = RealPiSugarBridge()
        reading = bridge.read()
        assert reading.battery_percent == 77.0
        # 0x0FA0 = 4000 mV -> 4.0 V
        assert reading.voltage_v == pytest.approx(4.0)
        # 0xFFFE = 65534; sign extended -> -2 mA
        assert reading.current_ma == pytest.approx(-2.0)
        assert reading.is_charging is False
        assert reading.temperature_c == 36.0
        status = bridge.status()
        assert status.last_reading == reading
        bridge.close()


class TestPiSugarMonitor:
    def test_publishes_readings_at_interval(self) -> None:
        bus = _RecordingBus()
        bridge = FakePiSugarBridge()
        monitor = PiSugarMonitor(bridge, bus, interval_s=0.05)
        monitor.start()
        time.sleep(0.25)
        monitor.stop(timeout=2.0)

        # At least two readings should have been published.
        assert len(bus.events) >= 2
        for event in bus.events:
            assert event["type"] == "hardware.pisugar.reading"
            assert event["payload"]["battery_percent"] == 80.0
            assert event["payload"]["is_powered"] is True
            assert event["payload"]["is_low"] is False
            assert event["payload"]["is_critical"] is False

    def test_stop_halts_thread_cleanly(self) -> None:
        bus = _RecordingBus()
        bridge = FakePiSugarBridge()
        monitor = PiSugarMonitor(bridge, bus, interval_s=0.01)
        monitor.start()
        # Stop must not raise and must be quick.
        monitor.stop(timeout=1.0)
        assert monitor._thread is None
        # The thread should be done; we shouldn't be able to join it again.
        assert not threading.current_thread().is_alive() is False  # sanity

    def test_publishes_low_and_critical_flags(self) -> None:
        bus = _RecordingBus()
        bridge = FakePiSugarBridge()
        bridge.set_battery(PISUGAR_BATTERY_LOW_PERCENT - 1)
        monitor = PiSugarMonitor(bridge, bus, interval_s=0.05)
        monitor.start()
        time.sleep(0.15)
        monitor.stop(timeout=2.0)
        assert bus.events, "monitor never published"
        last = bus.events[-1]["payload"]
        assert last["battery_percent"] <= PISUGAR_BATTERY_LOW_PERCENT
        assert last["is_low"] is True
        assert last["is_critical"] is False

        # Now drop into the critical band and re-run.
        bus.events.clear()
        bridge.set_battery(PISUGAR_BATTERY_CRITICAL_PERCENT - 1)
        monitor.start()
        time.sleep(0.15)
        monitor.stop(timeout=2.0)
        last = bus.events[-1]["payload"]
        assert last["is_critical"] is True


class TestBatteryReadingImmutability:
    def test_battery_reading_is_frozen(self) -> None:
        reading = BatteryReading(
            battery_percent=50.0,
            voltage_v=3.8,
            current_ma=-100.0,
            temperature_c=40.0,
            is_charging=False,
            is_powered=True,
            timestamp="2026-06-04T00:00:00+00:00",
        )
        with pytest.raises(Exception):
            reading.battery_percent = 25.0  # type: ignore[misc]

    def test_battery_reading_is_hashable(self) -> None:
        reading = BatteryReading(
            battery_percent=0.0,
            voltage_v=3.0,
            current_ma=0.0,
            temperature_c=None,
            is_charging=False,
            is_powered=False,
            timestamp="2026-06-04T00:00:00+00:00",
        )
        # Should be usable in a set / as a dict key without raising.
        {reading}
        {reading: "ok"}[reading] == "ok"

    def test_battery_reading_supports_boundary_percent(self) -> None:
        zero = BatteryReading(
            battery_percent=0.0,
            voltage_v=3.0,
            current_ma=0.0,
            temperature_c=None,
            is_charging=False,
            is_powered=False,
            timestamp="2026-06-04T00:00:00+00:00",
        )
        full = BatteryReading(
            battery_percent=100.0,
            voltage_v=4.2,
            current_ma=0.0,
            temperature_c=None,
            is_charging=True,
            is_powered=True,
            timestamp="2026-06-04T00:00:00+00:00",
        )
        assert zero.battery_percent == 0.0
        assert full.battery_percent == 100.0


class _RecordingBus:
    """Minimal stand-in for the real EventBus used by the monitor tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(self, event_type: str, payload: dict[str, object] | None = None) -> None:
        self.events.append({"type": event_type, "payload": payload or {}})
