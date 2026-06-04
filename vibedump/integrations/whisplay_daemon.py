"""Long-running Whisplay display refresh daemon.

This module is the entry point launched by ``vibedump-whisplay.service``.
M7-P1's bridge is fully implemented; this shell is intentionally minimal
until M8.5+ fleshes out the button poll + mascot frame push loop.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the whisplay display refresh loop.

    The real implementation will poll button state and push mascot frames
    to the ST7789 panel; for now we log a single startup line so the
    systemd unit has something to supervise.
    """
    logger.info("whisplay_daemon stub: bridge handles SPI/LED/buttons separately")


if __name__ == "__main__":
    main()
