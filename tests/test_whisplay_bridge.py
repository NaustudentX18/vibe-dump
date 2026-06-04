"""Tests for the Whisplay HAT bridge.

Coverage:
    * Fake bridge attribute surface (last_png, last_led, queueing, clamping).
    * Factory: prefer="fake" / prefer="auto" / prefer="real" failure modes.
    * Real bridge: WhisplayNotAvailable when a hardware dep is missing.
    * Lifecycle: close() is idempotent.
"""

from __future__ import annotations

import sys

import pytest

from vibedump.integrations.whisplay import (
    FakeWhisplayBridge,
    RealWhisplayBridge,
    WHISPLAY_BUTTON_LETTERS,
    WHISPLAY_BUTTON_PINS,
    WHISPLAY_HEIGHT,
    WHISPLAY_WIDTH,
    WhisplayNotAvailable,
    make_whisplay,
)


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def bridge() -> FakeWhisplayBridge:
    return FakeWhisplayBridge()


# ---------------------------------------------------------------------------
# 1. display_frame records bytes
# ---------------------------------------------------------------------------


def test_display_frame_stores_last_png(bridge: FakeWhisplayBridge) -> None:
    payload = PNG_MAGIC + b"hello-display"
    bridge.display_frame(payload)
    # Stored bytes match the input (bytes are immutable so aliasing is safe).
    assert bridge.last_png == payload
    assert bridge.display_count == 1


def test_display_frame_records_each_call(bridge: FakeWhisplayBridge) -> None:
    bridge.display_frame(b"first")
    bridge.display_frame(b"second")
    bridge.display_frame(b"third")
    assert bridge.last_png == b"third"
    assert bridge.display_count == 3


# ---------------------------------------------------------------------------
# 2. set_led records RGB
# ---------------------------------------------------------------------------


def test_set_led_records_rgb(bridge: FakeWhisplayBridge) -> None:
    bridge.set_led((10, 20, 30))
    assert bridge.last_led == (10, 20, 30)
    # History captures every change in order.
    assert bridge.led_history == [(10, 20, 30)]


def test_set_led_appends_to_history(bridge: FakeWhisplayBridge) -> None:
    bridge.set_led((1, 2, 3))
    bridge.set_led((4, 5, 6))
    bridge.set_led((7, 8, 9))
    assert bridge.led_history == [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    assert bridge.last_led == (7, 8, 9)


# ---------------------------------------------------------------------------
# 3. read_buttons returns empty set initially
# ---------------------------------------------------------------------------


def test_read_buttons_empty_by_default(bridge: FakeWhisplayBridge) -> None:
    assert bridge.read_buttons() == set()


def test_read_buttons_returns_only_known_letters(bridge: FakeWhisplayBridge) -> None:
    # All four canonical letters should be present in the protocol.
    for letter in WHISPLAY_BUTTON_LETTERS:
        assert letter in WHISPLAY_BUTTON_PINS
    assert WHISPLAY_BUTTON_LETTERS == frozenset({"A", "B", "C", "D"})


# ---------------------------------------------------------------------------
# 4. simulate_button + read_buttons
# ---------------------------------------------------------------------------


def test_simulate_button_appears_in_read(bridge: FakeWhisplayBridge) -> None:
    bridge.simulate_button("A")
    assert bridge.read_buttons() == {"A"}


def test_simulate_button_logs_call(bridge: FakeWhisplayBridge) -> None:
    bridge.simulate_button("A")
    bridge.simulate_button("B")
    assert bridge.button_log == ["A", "B"]


def test_simulate_button_rejects_unknown_letter(bridge: FakeWhisplayBridge) -> None:
    with pytest.raises(ValueError):
        bridge.simulate_button("X")
    with pytest.raises(ValueError):
        bridge.simulate_button("")


# ---------------------------------------------------------------------------
# 5. read_buttons consumes pending (debounce)
# ---------------------------------------------------------------------------


def test_read_buttons_does_not_replay_consumed_buttons(
    bridge: FakeWhisplayBridge,
) -> None:
    bridge.simulate_button("A")
    first = bridge.read_buttons()
    assert first == {"A"}
    # Already consumed: a second read with no new simulation must be empty.
    second = bridge.read_buttons()
    assert second == set()
    # And the queue is truly empty - we should not see the same A again.
    third = bridge.read_buttons()
    assert third == set()


# ---------------------------------------------------------------------------
# 6. make_whisplay(prefer="fake") returns the fake
# ---------------------------------------------------------------------------


def test_factory_prefer_fake_returns_fake() -> None:
    bridge = make_whisplay(prefer="fake")
    assert isinstance(bridge, FakeWhisplayBridge)


def test_factory_prefer_fake_is_fresh_instance() -> None:
    a = make_whisplay(prefer="fake")
    b = make_whisplay(prefer="fake")
    assert a is not b


def test_factory_unknown_prefer_raises() -> None:
    with pytest.raises(ValueError):
        make_whisplay(prefer="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. RealWhisplayBridge raises WhisplayNotAvailable on missing dep
# ---------------------------------------------------------------------------


def test_real_bridge_raises_when_spidev_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Setting sys.modules[name] = None causes `import name` to raise
    # ImportError on the next call, even if the package is installed.
    monkeypatch.setitem(sys.modules, "spidev", None)
    with pytest.raises(WhisplayNotAvailable) as excinfo:
        RealWhisplayBridge()
    # The error message must point the operator at the install script.
    assert "install_whisplay_prereqs.sh" in str(excinfo.value)


def test_real_bridge_raises_when_smbus2_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "smbus2", None)
    with pytest.raises(WhisplayNotAvailable) as excinfo:
        RealWhisplayBridge()
    assert "install_whisplay_prereqs.sh" in str(excinfo.value)


def test_real_bridge_raises_when_rpi_gpio_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "RPi", None)
    with pytest.raises(WhisplayNotAvailable) as excinfo:
        RealWhisplayBridge()
    assert "install_whisplay_prereqs.sh" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8. WhisplayNotAvailable is a RuntimeError
# ---------------------------------------------------------------------------


def test_whisplay_not_available_is_runtime_error() -> None:
    assert issubclass(WhisplayNotAvailable, RuntimeError)
    # Constructable with a custom message and re-raiseable.
    try:
        raise WhisplayNotAvailable("boom")
    except RuntimeError as exc:
        assert str(exc) == "boom"


# ---------------------------------------------------------------------------
# 9. make_whisplay(prefer="auto") falls back to fake when real unavailable
# ---------------------------------------------------------------------------


def test_factory_prefer_auto_falls_back_to_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the real bridge to fail as if spidev is not installed.
    monkeypatch.setitem(sys.modules, "spidev", None)
    bridge = make_whisplay(prefer="auto")
    assert isinstance(bridge, FakeWhisplayBridge)


def test_factory_prefer_real_propagates_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "spidev", None)
    # prefer="real" must NOT swallow the error; callers can then decide
    # whether to fall back or surface a fatal.
    with pytest.raises(WhisplayNotAvailable):
        make_whisplay(prefer="real")


# ---------------------------------------------------------------------------
# 10. close() is idempotent
# ---------------------------------------------------------------------------


def test_close_is_idempotent_on_fake() -> None:
    bridge = FakeWhisplayBridge()
    assert bridge._closed is False
    bridge.close()
    assert bridge._closed is True
    # Second call must not raise.
    bridge.close()
    bridge.close()
    assert bridge._closed is True


def test_fake_methods_raise_after_close() -> None:
    bridge = FakeWhisplayBridge()
    bridge.close()
    with pytest.raises(RuntimeError):
        bridge.display_frame(b"x")
    with pytest.raises(RuntimeError):
        bridge.set_led((0, 0, 0))
    with pytest.raises(RuntimeError):
        bridge.read_buttons()
    with pytest.raises(RuntimeError):
        bridge.simulate_button("A")


# ---------------------------------------------------------------------------
# 11. Multiple simulate_button calls queue up; read returns the union
# ---------------------------------------------------------------------------


def test_multiple_simulate_button_calls_queue_union(
    bridge: FakeWhisplayBridge,
) -> None:
    bridge.simulate_button("A")
    bridge.simulate_button("B")
    bridge.simulate_button("A")
    bridge.simulate_button("C")
    bridge.simulate_button("D")
    # Duplicates collapse into a set, but the log records every call.
    assert bridge.read_buttons() == {"A", "B", "C", "D"}
    assert bridge.button_log == ["A", "B", "A", "C", "D"]


def test_pending_drains_completely_after_read(bridge: FakeWhisplayBridge) -> None:
    bridge.simulate_button("A")
    bridge.simulate_button("B")
    bridge.read_buttons()
    # Drain completely - nothing left.
    assert bridge.read_buttons() == set()
    # A fresh press re-populates the queue.
    bridge.simulate_button("C")
    assert bridge.read_buttons() == {"C"}


# ---------------------------------------------------------------------------
# 12. set_led clamps out-of-range RGB to 0-255
# ---------------------------------------------------------------------------


def test_set_led_clamps_high_values(bridge: FakeWhisplayBridge) -> None:
    bridge.set_led((1000, -1, 256))
    assert bridge.last_led == (255, 0, 255)


def test_set_led_clamps_low_values(bridge: FakeWhisplayBridge) -> None:
    bridge.set_led((-50, 0, 1000))
    assert bridge.last_led == (0, 0, 255)


def test_set_led_clamps_in_place_in_history(bridge: FakeWhisplayBridge) -> None:
    bridge.set_led((-1, 0, 256))
    bridge.set_led((255, 255, 255))
    bridge.set_led((128, 64, 32))
    assert bridge.led_history == [
        (0, 0, 255),
        (255, 255, 255),
        (128, 64, 32),
    ]


# ---------------------------------------------------------------------------
# Constants sanity (also protects the pin map from silent drift)
# ---------------------------------------------------------------------------


def test_constants_match_whisplay_schematic() -> None:
    assert WHISPLAY_WIDTH == 240
    assert WHISPLAY_HEIGHT == 280
    assert WHISPLAY_BUTTON_PINS == {"A": 5, "B": 6, "C": 16, "D": 24}
    # Each pin is a valid BCM GPIO number (0-27 on a Pi Zero 2 W).
    for letter, pin in WHISPLAY_BUTTON_PINS.items():
        assert 0 <= pin <= 27, f"{letter} pin {pin} out of range"


def test_factory_default_is_auto() -> None:
    # The signature default is prefer="auto" - verify by patching the real
    # bridge to fail and confirming we land on the fake without an arg.
    import vibedump.integrations.whisplay as ws

    def _explode() -> FakeWhisplayBridge:
        raise WhisplayNotAvailable("test")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(ws, "RealWhisplayBridge", _explode)
        bridge = make_whisplay()  # no arg -> default prefer
        assert isinstance(bridge, FakeWhisplayBridge)
    finally:
        monkey.undo()
