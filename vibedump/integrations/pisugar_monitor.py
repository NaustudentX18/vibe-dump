"""PiSugar battery poller entry point.

Launched by ``vibedump-pisugar.service``; delegates to
``PiSugarMonitor.run_forever`` which is currently a stub pending the M8.5+
battery polling implementation.
"""

from __future__ import annotations

from .pisugar import PiSugarMonitor


def main() -> None:
    """Run the PiSugar monitor loop forever."""
    PiSugarMonitor().run_forever()


if __name__ == "__main__":
    main()
