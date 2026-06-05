#!/usr/bin/env python3
"""Weekly self-evolution cron entry point (M10 Pillar 3).

Extracts lessons from recent dumps and appends them to the lesson store.
Safe to run on a schedule via systemd timer or cron.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from vibedump.agent.evolution import EvolutionHook
from vibedump.database import Database
from vibedump.memory.lessons import LessonStore

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Vibe-Dump weekly evolution pass")
    parser.add_argument(
        "--db",
        default=os.environ.get("VIBEDUMP_DATABASE_PATH", "data/vibedump.sqlite3"),
        help="SQLite database path",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max dumps to scan")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("database not found: %s", db_path)
        return 1

    db = Database(str(db_path))
    db.initialize()
    lessons = LessonStore(db)
    hook = EvolutionHook(lesson_store=lessons)

    dumps = db.list_dumps(limit=args.limit)
    learned = 0
    for dump in dumps:
        blueprint = db.get_latest_blueprint(dump.id)
        if blueprint is None:
            continue
        hook.on_run_complete(
            dump_id=dump.id,
            transcript="\n".join(t.text for t in db.list_turns(dump.id)),
            blueprint=blueprint.markdown,
        )
        learned += 1

    logger.info("evolution pass complete: %d dumps processed", learned)
    db.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
