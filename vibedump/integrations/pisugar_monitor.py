"""PiSugar battery poller entry point.

Launched by ``vibedump-pisugar.service``; delegates to
:class:`PiSugarMonitor.run_forever`.
"""

from __future__ import annotations

import logging

from vibedump.events import EventBus
from vibedump.integrations.pisugar import PiSugarMonitor, make_pisugar

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the PiSugar monitor loop forever."""
    bus = EventBus()
    bridge = make_pisugar(prefer="auto")
    monitor = PiSugarMonitor(bridge, bus)
    logger.info("PiSugar monitor starting (prefer=auto bridge=%s)", type(bridge).__name__)
    monitor.run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
