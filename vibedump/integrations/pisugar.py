"""PiSugar battery / power telemetry bridge.

Exposes a thin hardware abstraction that mirrors the Whisplay bridge pattern:
``PiSugarBridge`` is the Protocol, ``RealPiSugarBridge`` talks to the I2C bus,
``FakePiSugarBridge`` keeps readings in memory for tests, and ``make_pisugar``
selects between them.

A daemon-thread ``PiSugarMonitor`` can be attached to an ``EventBus`` to publish
``hardware.pisugar.reading`` events at a fixed interval.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# I2C bus + address used by PiSugar 3 HATs on Raspberry Pi OS.
PISUGAR_I2C_BUS = 1
PISUGAR_I2C_ADDR = 0x57

# Register map (PiSugar 3). 0x2a is battery percent; 0x2b/0x2c is a 16-bit
# voltage in mV; 0x33/0x34 is a signed 16-bit current in mA. 0x04 holds the
# board temperature on firmware that exposes it.
PISUGAR_REG_BATTERY_PERCENT = 0x2A
PISUGAR_REG_VOLTAGE_HI = 0x2B
PISUGAR_REG_VOLTAGE_LO = 0x2C
PISUGAR_REG_CURRENT_HI = 0x33
PISUGAR_REG_CURRENT_LO = 0x34
PISUGAR_REG_TEMPERATURE = 0x04

# Polling cadence + thresholds for the optional monitor.
PISUGAR_DEFAULT_INTERVAL_S = 30.0
PISUGAR_BATTERY_LOW_PERCENT = 20.0
PISUGAR_BATTERY_CRITICAL_PERCENT = 10.0


class PiSugarNotAvailable(RuntimeError):
    """Raised when the real PiSugar hardware (or its driver) cannot be used."""


@dataclass(frozen=True, slots=True)
class BatteryReading:
    """A single battery / power snapshot from the PiSugar HAT."""

    battery_percent: float
    voltage_v: float
    current_ma: float
    temperature_c: float | None
    is_charging: bool
    is_powered: bool
    timestamp: str


@dataclass(frozen=True, slots=True)
class PiSugarStatus:
    """Static info about the PiSugar device plus the most recent reading."""

    model: str
    firmware: str
    i2c_address: int
    last_reading: BatteryReading | None


class PiSugarBridge(Protocol):
    """Minimal contract any PiSugar integration must satisfy."""

    def read(self) -> BatteryReading: ...

    def status(self) -> PiSugarStatus: ...

    def close(self) -> None: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_percent(percent: float) -> float:
    if percent < 0.0:
        return 0.0
    if percent > 100.0:
        return 100.0
    return float(percent)


class FakePiSugarBridge:
    """In-memory PiSugar used by tests and fake-first dev runs."""

    def __init__(self) -> None:
        self._battery_percent: float = 80.0
        self._voltage_v: float = 4.05
        self._current_ma: float = -150.0
        self._temperature_c: float | None = 42.0
        self._is_charging: bool = False
        self._is_powered: bool = True
        self._last_reading: BatteryReading | None = None

    def read(self) -> BatteryReading:
        reading = BatteryReading(
            battery_percent=self._battery_percent,
            voltage_v=self._voltage_v,
            current_ma=self._current_ma,
            temperature_c=self._temperature_c,
            is_charging=self._is_charging,
            is_powered=self._is_powered,
            timestamp=_now_iso(),
        )
        self._last_reading = reading
        return reading

    def status(self) -> PiSugarStatus:
        return PiSugarStatus(
            model="PiSugar 3",
            firmware="fake",
            i2c_address=PISUGAR_I2C_ADDR,
            last_reading=self._last_reading,
        )

    def close(self) -> None:
        # Nothing to release.
        return None

    # Test / fakes affordances -------------------------------------------------
    def set_battery(self, percent: float) -> None:
        self._battery_percent = _clamp_percent(percent)
        if self._battery_percent < 100.0 and not self._is_charging:
            # Tethered chargers rarely discharge while USB is plugged in.
            self._current_ma = abs(self._current_ma) * -1 or -50.0

    def set_charging(self, charging: bool) -> None:
        self._is_charging = charging
        if charging:
            self._is_powered = True
            self._current_ma = abs(self._current_ma) or 300.0
        elif self._is_powered:
            # Still on USB, just not actively charging.
            self._current_ma = -10.0


class RealPiSugarBridge:
    """Reads PiSugar telemetry over I2C using ``smbus2``.

    The library is imported lazily so the module is safe to import on machines
    that don't have the kernel-side I2C drivers or the Python bindings.
    """

    def __init__(
        self,
        bus_number: int = PISUGAR_I2C_BUS,
        address: int = PISUGAR_I2C_ADDR,
    ) -> None:
        self._bus_number = bus_number
        self._address = address
        self._bus: Any = None
        self._last_reading: BatteryReading | None = None
        self._lock = threading.Lock()
        self._open_bus(bus_number)

    def _open_bus(self, bus_number: int) -> None:
        try:
            from smbus2 import SMBus  # type: ignore[import-not-found]
        except Exception as exc:  # ImportError or environment without smbus2
            raise PiSugarNotAvailable(
                "smbus2 is not installed; cannot talk to PiSugar hardware"
            ) from exc
        try:
            self._bus = SMBus(bus_number)
        except Exception as exc:
            raise PiSugarNotAvailable(
                f"failed to open I2C bus {bus_number}: {exc}"
            ) from exc

    def _read_byte(self, register: int) -> int:
        assert self._bus is not None
        with self._lock:
            return int(self._bus.read_byte_data(self._address, register))

    def read(self) -> BatteryReading:
        try:
            percent = float(self._read_byte(PISUGAR_REG_BATTERY_PERCENT))
            voltage_raw = (
                self._read_byte(PISUGAR_REG_VOLTAGE_HI) << 8
            ) | self._read_byte(PISUGAR_REG_VOLTAGE_LO)
            current_raw = (
                self._read_byte(PISUGAR_REG_CURRENT_HI) << 8
            ) | self._read_byte(PISUGAR_REG_CURRENT_LO)
            if current_raw >= 0x8000:
                current_raw -= 0x10000
            try:
                temperature_c = float(self._read_byte(PISUGAR_REG_TEMPERATURE))
            except PiSugarNotAvailable:
                temperature_c = None
        except PiSugarNotAvailable:
            raise
        except Exception as exc:
            raise PiSugarNotAvailable(
                f"I2C read failed for PiSugar @ 0x{self._address:02x}: {exc}"
            ) from exc

        reading = BatteryReading(
            battery_percent=_clamp_percent(percent),
            voltage_v=voltage_raw / 1000.0,
            current_ma=float(current_raw),
            temperature_c=temperature_c,
            is_charging=current_raw > 0,
            is_powered=True,
            timestamp=_now_iso(),
        )
        self._last_reading = reading
        return reading

    def status(self) -> PiSugarStatus:
        return PiSugarStatus(
            model="PiSugar 3",
            firmware="unknown",
            i2c_address=self._address,
            last_reading=self._last_reading,
        )

    def close(self) -> None:
        with self._lock:
            if self._bus is not None:
                try:
                    self._bus.close()
                except Exception:  # pragma: no cover - best effort
                    logger.debug("PiSugar I2C bus close failed", exc_info=True)
                self._bus = None


def _i2c_path_available() -> bool:
    """Return True if the PiSugar I2C device node is present.

    Smoke gate: short-circuits the auto factory to fake on dev boxes
    where /dev/i2c-1 is absent, avoiding the smbus2 import + I2C open
    attempt on every cold start.
    """
    from vibedump.integrations.hardware_probe import check_paths

    return all(
        r.available
        for r in check_paths()
        if r.name == "i2c"
    )


def _try_real_bridge() -> RealPiSugarBridge | None:
    try:
        return RealPiSugarBridge()
    except PiSugarNotAvailable as exc:
        logger.info("Falling back to FakePiSugarBridge: %s", exc)
        return None


def make_pisugar(prefer: str = "auto") -> PiSugarBridge:
    """Pick a bridge implementation.

    ``prefer`` values:
    * ``"auto"``  — try the real bridge, fall back to fake if unavailable.
    * ``"real"``  — require the real bridge; raise on failure.
    * ``"fake"``  — always return the in-memory bridge.
    """
    if prefer == "fake":
        return FakePiSugarBridge()
    if prefer == "real":
        return RealPiSugarBridge()
    if prefer == "auto":
        # Smoke gate: skip the smbus2 import + I2C open when the bus
        # device node isn't visible to the kernel.
        if not _i2c_path_available():
            return FakePiSugarBridge()
        return _try_real_bridge() or FakePiSugarBridge()
    raise ValueError(f"unknown pisugar prefer mode: {prefer!r}")


class PiSugarMonitor:
    """Background poller that publishes PiSugar readings to an EventBus."""

    EVENT_TYPE = "hardware.pisugar.reading"

    def __init__(
        self,
        bridge: PiSugarBridge,
        bus: Any,
        interval_s: float = PISUGAR_DEFAULT_INTERVAL_S,
    ) -> None:
        self._bridge = bridge
        self._bus = bus
        self._interval_s = max(0.01, float(interval_s))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="PiSugarMonitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def run_forever(self) -> None:
        """Block until :meth:`stop` is called (standalone daemon entry)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        finally:
            self.stop()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                reading = self._bridge.read()
                self._publish(reading)
            except Exception:
                logger.exception("PiSugarMonitor failed to read bridge")
            # Sleep in small chunks so stop() is responsive.
            self._sleep(self._interval_s)

    def _publish(self, reading: BatteryReading) -> None:
        publish = getattr(self._bus, "publish", None)
        if publish is None:
            return
        try:
            publish(
                self.EVENT_TYPE,
                {
                    "battery_percent": reading.battery_percent,
                    "voltage_v": reading.voltage_v,
                    "current_ma": reading.current_ma,
                    "temperature_c": reading.temperature_c,
                    "is_charging": reading.is_charging,
                    "is_powered": reading.is_powered,
                    "timestamp": reading.timestamp,
                    "is_low": reading.battery_percent <= PISUGAR_BATTERY_LOW_PERCENT,
                    "is_critical": reading.battery_percent
                    <= PISUGAR_BATTERY_CRITICAL_PERCENT,
                },
            )
        except Exception:
            logger.exception("PiSugarMonitor failed to publish event")

    def _sleep(self, seconds: float) -> None:
        # Wait in 100ms slices so stop() returns quickly.
        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop_event.wait(min(0.1, remaining))


__all__ = [
    "PISUGAR_I2C_BUS",
    "PISUGAR_I2C_ADDR",
    "PISUGAR_DEFAULT_INTERVAL_S",
    "PISUGAR_BATTERY_LOW_PERCENT",
    "PISUGAR_BATTERY_CRITICAL_PERCENT",
    "PiSugarNotAvailable",
    "BatteryReading",
    "PiSugarStatus",
    "PiSugarBridge",
    "FakePiSugarBridge",
    "RealPiSugarBridge",
    "PiSugarMonitor",
    "make_pisugar",
]
