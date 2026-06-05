"""Long-running Whisplay display refresh daemon.

Entry point for ``vibedump-whisplay.service``. Polls physical buttons,
pushes Dumpi mascot frames to the ST7789 panel, and mirrors device state
on the WS2812 LED.
"""

from __future__ import annotations

import logging
import signal
import time

from vibedump.mascot_renderer import MascotRenderer
from vibedump.state import DeviceState

from .whisplay import WHISPLAY_BUTTON_PINS, make_whisplay

logger = logging.getLogger(__name__)

# Button D is the primary push-to-talk control on the HAT face.
PTT_BUTTON = "D"

_STATE_LED: dict[str, tuple[int, int, int]] = {
    "idle": (183, 138, 58),
    "listening": (74, 214, 255),
    "thinking": (160, 124, 255),
    "speaking": (88, 224, 164),
    "error": (255, 100, 124),
    "ready": (88, 224, 164),
    "draft": (136, 136, 136),
}


class WhisplayDaemon:
    """Poll loop for Whisplay HAT LCD + buttons + LED."""

    def __init__(
        self,
        *,
        poll_interval_s: float = 0.05,
        frame_interval_s: float = 1.0,
    ) -> None:
        self._bridge = make_whisplay(prefer="auto")
        self._renderer = MascotRenderer()
        self._poll_interval_s = poll_interval_s
        self._frame_interval_s = frame_interval_s
        self._stop = False
        self._device_state = DeviceState.IDLE.value
        self._last_frame_at = 0.0

    def stop(self) -> None:
        self._stop = True

    def set_device_state(self, state: str) -> None:
        self._device_state = state

    def _push_frame(self) -> None:
        frame = self._renderer.render(self._device_state)
        if frame.png_bytes:
            self._bridge.display_frame(frame.png_bytes)
        rgb = _STATE_LED.get(self._device_state, _STATE_LED["idle"])
        self._bridge.set_led(rgb)

    def run_forever(self) -> None:
        logger.info("Whisplay daemon starting (bridge=%s)", type(self._bridge).__name__)
        self._push_frame()
        while not self._stop:
            try:
                pressed = self._bridge.read_buttons()
                if PTT_BUTTON in pressed:
                    logger.debug("button %s pressed", PTT_BUTTON)
                now = time.monotonic()
                if now - self._last_frame_at >= self._frame_interval_s:
                    self._push_frame()
                    self._last_frame_at = now
            except Exception:
                logger.exception("Whisplay poll iteration failed")
            time.sleep(self._poll_interval_s)
        try:
            self._bridge.close()
        except Exception:
            pass
        logger.info("Whisplay daemon stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    daemon = WhisplayDaemon()
    signal.signal(signal.SIGTERM, lambda *_: daemon.stop())
    signal.signal(signal.SIGINT, lambda *_: daemon.stop())
    daemon.run_forever()


if __name__ == "__main__":
    main()
