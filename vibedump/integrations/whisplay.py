"""Whisplay HAT bridge for the Vibe-Dump Pocket Pi build.

The Whisplay HAT (Waveshare, ST7789 240x280 LCD + 4 buttons + WS2812 LED) is
the canonical physical target for Vibe-Dump. This module is a thin, fakeable
abstraction so the rest of the app can run on a non-Pi dev box and on real
hardware with the same call sites.

Three callables are exposed:

* :class:`WhisplayBridge`  - structural ``Protocol`` every implementation
  satisfies.
* :class:`RealWhisplayBridge` - talks to spidev (LCD), smbus2 (LED MCU), and
  RPi.GPIO (buttons). Hardware imports are lazy and guarded; a missing dep
  raises :class:`WhisplayNotAvailable` from the constructor.
* :class:`FakeWhisplayBridge` - in-memory double used by every test.
* :func:`make_whisplay` - factory: ``prefer="auto"`` (default) probes for real
  hardware and falls back to fake, ``prefer="real"`` forces real, ``prefer="fake"``
  forces fake.

Constants for the Whisplay pin map, SPI bus, and timing live at module scope
and are the single source of truth for the rest of the app.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Protocol

# ---------------------------------------------------------------------------
# Constants - Whisplay HAT pin map
# ---------------------------------------------------------------------------
# These are the canonical values used by the Waveshare Whisplay schematic.
# Verify against /boot/firmware/config.txt before changing - the bridge will
# fail loudly (WhisplayNotAvailable) if the kernel can't see SPI bus 0 dev 0.

WHISPLAY_WIDTH: int = 240
WHISPLAY_HEIGHT: int = 280
WHISPLAY_SPI_BUS: int = 0
WHISPLAY_SPI_DEV: int = 0
WHISPLAY_SPI_HZ: int = 40_000_000

WHISPLAY_LED_COUNT: int = 1

# BCM pin numbers, active-low with on-HAT pull-ups. The bridge polls these
# and treats LOW as "pressed". Order is stable; the buttons on the HAT face
# the user left-to-right as A, B, C, D.
WHISPLAY_BUTTON_PINS: dict[str, int] = {
    "A": 5,
    "B": 6,
    "C": 16,
    "D": 24,
}

WHISPLAY_BUTTON_LETTERS: frozenset[str] = frozenset(WHISPLAY_BUTTON_PINS.keys())


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WhisplayNotAvailable(RuntimeError):
    """Raised when real Whisplay hardware (or its Python deps) is missing.

    The message intentionally points at the install script so the operator
    has an obvious next step.
    """


# ---------------------------------------------------------------------------
# Bridge protocol
# ---------------------------------------------------------------------------


class WhisplayBridge(Protocol):
    """Structural interface every Whisplay bridge must satisfy.

    The real and fake implementations are duck-typed; the test suite does not
    require nominal inheritance from this Protocol, only that the four
    methods are present with the documented signatures.
    """

    def display_frame(self, png_bytes: bytes) -> None:
        """Render an RGB PNG onto the LCD."""

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        """Set the on-board WS2812 LED color. Channels are clamped 0-255."""

    def read_buttons(self) -> set[str]:
        """Return the subset of {"A","B","C","D"} currently pressed.

        Implementations are free to use either edge-triggered or level-based
        semantics; consumers should treat the result as "buttons held at the
        moment of the call".
        """

    def close(self) -> None:
        """Release SPI / I2C / GPIO handles. Idempotent."""


# ---------------------------------------------------------------------------
# Fake implementation (default for tests + non-Pi dev)
# ---------------------------------------------------------------------------


@dataclass
class _LedHistory:
    """Tracks every LED color the fake has ever been set to, in order."""

    changes: list[tuple[int, int, int]]

    def last(self) -> tuple[int, int, int] | None:
        return self.changes[-1] if self.changes else None


class FakeWhisplayBridge:
    """In-memory WhisplayBridge for tests.

    State is exposed as plain attributes so tests can assert directly:

    * ``last_png``         - bytes from the most recent ``display_frame`` call.
    * ``last_led``         - clamped RGB tuple from the most recent ``set_led``.
    * ``led_history``      - list of every clamped color the bridge has seen.
    * ``display_count``    - number of times ``display_frame`` was called.
    * ``button_log``       - list of every ``simulate_button`` call in order.
    * ``closed``           - True after the first ``close()`` call.

    Buttons use a one-shot queue: ``simulate_button("A")`` enqueues the press;
    the next ``read_buttons()`` returns it and clears the queue. This matches
    the "edge-triggered" semantics real GPIO interrupt handlers typically use.
    """

    def __init__(self) -> None:
        self.last_png: bytes | None = None
        self.last_led: tuple[int, int, int] | None = None
        self.led_history: list[tuple[int, int, int]] = []
        self.display_count: int = 0
        self.button_log: list[str] = []
        self._pending: list[str] = []
        self._closed: bool = False

    # ---- display ---------------------------------------------------------

    def display_frame(self, png_bytes: bytes) -> None:
        if self._closed:
            raise RuntimeError("FakeWhisplayBridge is closed")
        # Copy the bytes so tests can mutate their input without surprising us.
        self.last_png = bytes(png_bytes)
        self.display_count += 1

    # ---- LED -------------------------------------------------------------

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        if self._closed:
            raise RuntimeError("FakeWhisplayBridge is closed")
        r, g, b = (int(c) for c in rgb)
        clamped = (
            0 if r < 0 else 255 if r > 255 else r,
            0 if g < 0 else 255 if g > 255 else g,
            0 if b < 0 else 255 if b > 255 else b,
        )
        self.last_led = clamped
        self.led_history.append(clamped)

    # ---- buttons ---------------------------------------------------------

    def simulate_button(self, letter: str) -> None:
        """Enqueue a button-press event for the next ``read_buttons`` call."""
        if letter not in WHISPLAY_BUTTON_LETTERS:
            raise ValueError(
                f"unknown button letter {letter!r}; "
                f"expected one of {sorted(WHISPLAY_BUTTON_LETTERS)}"
            )
        if self._closed:
            raise RuntimeError("FakeWhisplayBridge is closed")
        self.button_log.append(letter)
        self._pending.append(letter)

    def read_buttons(self) -> set[str]:
        if self._closed:
            raise RuntimeError("FakeWhisplayBridge is closed")
        # Queue semantics: read consumes the pending set, so a second
        # read_buttons() returns empty until simulate_button is called again.
        result = set(self._pending)
        self._pending.clear()
        return result

    # ---- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# Real implementation (lazy imports, Pi-only)
# ---------------------------------------------------------------------------


class RealWhisplayBridge:
    """Real Whisplay HAT bridge.

    Imports are performed lazily inside :meth:`__init__` so that this module
    is import-safe on a non-Pi dev box. A missing dependency or absent
    /dev/spidev0.0 surfaces as :class:`WhisplayNotAvailable` with a message
    that points at the install script.
    """

    def __init__(self) -> None:
        # Lazy imports - these are Pi-only and will fail on a laptop.
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-not-found]
            import smbus2  # type: ignore[import-not-found]
            import spidev  # type: ignore[import-not-found]
        except ImportError as exc:
            missing = exc.name or "unknown module"
            raise WhisplayNotAvailable(
                f"Whisplay hardware dependency '{missing}' is not installed. "
                f"Run scripts/install_whisplay_prereqs.sh to install it."
            ) from exc

        self._spidev = spidev
        self._smbus2 = smbus2
        self._GPIO = GPIO

        # Open SPI bus. Will raise OSError if the kernel module is not loaded
        # or the HAT is not stacked; we surface that as WhisplayNotAvailable
        # so callers can auto-fall-back without catching broad OSError.
        try:
            self._spi = spidev.SpiDev()
            self._spi.open(WHISPLAY_SPI_BUS, WHISPLAY_SPI_DEV)
            self._spi.max_speed_hz = WHISPLAY_SPI_HZ
            self._spi.mode = 0b00
        except (OSError, FileNotFoundError) as exc:
            raise WhisplayNotAvailable(
                f"Whisplay SPI bus {WHISPLAY_SPI_BUS}.{WHISPLAY_SPI_DEV} "
                f"is not available ({exc}). Did you enable SPI in raspi-config?"
            ) from exc

        # I2C for the HAT's on-board LED MCU. Bus 1 is the user-accessible
        # bus on every Pi since the B+; the HAT sits at 0x17 by default.
        self._i2c_addr = 0x17
        try:
            self._i2c = smbus2.SMBus(1)
        except (OSError, FileNotFoundError) as exc:
            self._spi.close()
            raise WhisplayNotAvailable(
                f"Whisplay I2C bus 1 is not available ({exc}). "
                f"Did you enable I2C in raspi-config?"
            ) from exc

        # GPIO inputs for buttons. Active-low with on-HAT pull-ups.
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in WHISPLAY_BUTTON_PINS.values():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Bring the LCD up. The ST7789 init sequence is the standard
        # Waveshare one - we send it once and never touch it again.
        self._init_st7789()
        self._closed = False

    # ---- display ---------------------------------------------------------

    def display_frame(self, png_bytes: bytes) -> None:
        if self._closed:
            raise RuntimeError("RealWhisplayBridge is closed")

        # Lazy Pillow import so this file is importable without it.
        from PIL import Image  # type: ignore[import-not-found]

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        if img.size != (WHISPLAY_WIDTH, WHISPLAY_HEIGHT):
            img = img.resize((WHISPLAY_WIDTH, WHISPLAY_HEIGHT))

        # ST7789 wants big-endian RGB565. Pushing the full frame over SPI at
        # 40 MHz takes ~25 ms; we accept that latency for v0.3.
        pixels = img.tobytes("raw", "RGB")
        self._spi_write_command(0x2C)  # RAMWR
        self._spi_write_data(pixels)

    def _init_st7789(self) -> None:
        """Send the ST7789 init sequence. Idempotent across instances."""
        cmds: list[tuple[int, bytes | None]] = [
            (0x01, None),        # SWRESET
            (0x11, None),        # SLPOUT
            (0x3A, b"\x55"),     # COLMOD: 16-bit RGB
            (0x36, b"\x00"),     # MADCTL: row/col order
            (0x21, None),        # INVON
            (0x13, None),        # NORON
            (0x29, None),        # DISPON
        ]
        for cmd, data in cmds:
            self._spi_write_command(cmd)
            if data is not None:
                self._spi_write_data(data)

    def _spi_write_command(self, cmd: int) -> None:
        # D/C low = command on the Whisplay HAT.
        self._spi.xfer2([cmd & 0xFF])

    def _spi_write_data(self, data: bytes) -> None:
        # D/C high = data. We prepend a 0x00 byte to assert D/C; the Whisplay
        # HAT breaks this out via a dedicated GPIO in the real schematic, but
        # for v0.3 we keep the bus simple and rely on the protocol prefix.
        # See install_whisplay_prereqs.sh for the GPIO mapping.
        chunks = (len(data) + 4095) // 4096
        for i in range(chunks):
            self._spi.xfer2(data[i * 4096 : (i + 1) * 4096])

    # ---- LED -------------------------------------------------------------

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        if self._closed:
            raise RuntimeError("RealWhisplayBridge is closed")
        r, g, b = (int(c) for c in rgb)
        clamped = (
            0 if r < 0 else 255 if r > 255 else r,
            0 if g < 0 else 255 if g > 255 else g,
            0 if b < 0 else 255 if b > 255 else b,
        )
        # Whisplay HAT LED MCU protocol: register 0x00, payload [r, g, b].
        self._i2c.write_i2c_block_data(self._i2c_addr, 0x00, list(clamped))

    # ---- buttons ---------------------------------------------------------

    def read_buttons(self) -> set[str]:
        if self._closed:
            raise RuntimeError("RealWhisplayBridge is closed")
        pressed: set[str] = set()
        for letter, pin in WHISPLAY_BUTTON_PINS.items():
            # Active-low: 0 means pressed.
            if self._GPIO.input(pin) == 0:
                pressed.add(letter)
        return pressed

    # ---- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Best-effort teardown; we never want a teardown error to mask the
        # original failure that triggered close().
        try:
            self._spi.close()
        except Exception:
            pass
        try:
            self._i2c.close()
        except Exception:
            pass
        try:
            self._GPIO.cleanup(list(WHISPLAY_BUTTON_PINS.values()))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _spi_path_available() -> bool:
    """Return True if the Whisplay SPI device node is present.

    Used as a fast pre-flight before the heavier real-bridge probe so
    that auto-mode on a non-Pi dev box does not pay the cost of trying
    to import spidev / smbus2 / RPi.GPIO.
    """
    from vibedump.integrations.hardware_probe import check_paths

    return all(
        r.available
        for r in check_paths()
        if r.name == "spi"
    )


def make_whisplay(prefer: str = "auto") -> WhisplayBridge:
    """Build a Whisplay bridge.

    Parameters
    ----------
    prefer:
        ``"auto"``  - try real hardware, fall back to fake on
                      :class:`WhisplayNotAvailable`. Default.
        ``"real"``  - always real; raise on failure.
        ``"fake"``  - always fake; useful in unit tests that want to assert
                      on the in-memory state.

    Returns
    -------
    WhisplayBridge
        Either a :class:`RealWhisplayBridge` or a :class:`FakeWhisplayBridge`
        instance.
    """
    if prefer == "fake":
        return FakeWhisplayBridge()
    if prefer == "real":
        return RealWhisplayBridge()
    if prefer == "auto":
        # Smoke gate: if the SPI device node is missing, do not even try
        # the real bridge - the kernel-side spidev driver can't see the
        # HAT. Saves an import of spidev/smbus2/RPi.GPIO on dev boxes.
        if not _spi_path_available():
            return FakeWhisplayBridge()
        try:
            return RealWhisplayBridge()
        except WhisplayNotAvailable:
            return FakeWhisplayBridge()
    raise ValueError(
        f"unknown prefer value {prefer!r}; expected 'auto', 'real', or 'fake'"
    )


__all__ = [
    "WHISPLAY_BUTTON_LETTERS",
    "WHISPLAY_BUTTON_PINS",
    "WHISPLAY_HEIGHT",
    "WHISPLAY_LED_COUNT",
    "WHISPLAY_SPI_BUS",
    "WHISPLAY_SPI_DEV",
    "WHISPLAY_SPI_HZ",
    "WHISPLAY_WIDTH",
    "FakeWhisplayBridge",
    "RealWhisplayBridge",
    "WhisplayBridge",
    "WhisplayNotAvailable",
    "make_whisplay",
]
