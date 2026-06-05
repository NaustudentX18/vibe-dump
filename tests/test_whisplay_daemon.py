"""Tests for the Whisplay hardware daemon poll loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vibedump.integrations.whisplay_daemon import WhisplayDaemon


def test_daemon_pushes_initial_frame() -> None:
    bridge = MagicMock()
    bridge.read_buttons.return_value = set()
    renderer = MagicMock()
    renderer.render.return_value = MagicMock(
        png_bytes=b"\x89PNG\r\n\x1a\n",
        state="idle",
    )

    with patch("vibedump.integrations.whisplay_daemon.make_whisplay", return_value=bridge):
        with patch("vibedump.integrations.whisplay_daemon.MascotRenderer", return_value=renderer):
            daemon = WhisplayDaemon(poll_interval_s=0.001, frame_interval_s=10.0)
            daemon._stop = True  # exit immediately after first iteration setup
            daemon.run_forever()

    bridge.display_frame.assert_called()
    bridge.set_led.assert_called()
