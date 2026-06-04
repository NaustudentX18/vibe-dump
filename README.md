# Vibe-Dump

Vibe-Dump is a pocket AI spec goblin for turning chaotic voice ideas into build-ready software blueprints.

This repository currently implements the **Milestone 0/1 fake-first scaffold** from `/home/pi/VIBE_DUMP_BUILD_HANDOVER_2026-06-04.md`.

## Current status

Implemented now:

- Python package scaffold under `vibedump/`.
- Config examples: `.env.example` and `config.example.json`.
- Fake hardware, mascot, STT, LLM, and TTS components.
- SQLite persistence with:
  - WAL mode
  - foreign key enforcement
  - serialized writes through a small locked wrapper
  - dump/turn/blueprint/chunk/provider/event/profile tables
  - FTS5 table for MVP RAG search
  - cascade cleanup for dump deletion
- FTS-backed `RagMemory` chunking/search.
- Fake pipeline that stores transcript, blueprint, and searchable memory.
- Mobile dashboard placeholder at `vibedump/static/dashboard.html`.
- Tests for database, RAG memory, fake providers, fake pipeline, and XP profile seed.

Not implemented yet:

- Real Whisplay/PiSugar hardware control.
- Real cloud/local provider adapters.
- FastAPI dashboard routes beyond placeholders.
- Google Drive/rclone sync.
- PIL mascot rendering.

## Quick verification

```bash
cd /home/pi/vibe-dump
python -m pytest -q
python -c "import vibedump; from vibedump.database import Database; from vibedump.ragmemory import RagMemory; print(vibedump.__version__)"
```

Expected test result for this milestone: `7 passed`.

## Development notes

- Keep provider secrets in `.env` or local config only; never commit real keys.
- Tests must not touch real Whisplay/PiSugar hardware.
- The Pi Zero 2 W target has 512MB RAM, so keep dependencies lean.
- Later web work can install the optional `web` extra (`fastapi`, `uvicorn`).

## Next milestones

1. Expand FastAPI routes and mobile-first dashboard.
2. Add provider registry health endpoints and configurable local/cloud adapters.
3. Implement the full agent pipeline state machine.
4. Add PIL mascot frames and XP/achievement UI.
5. Add sync/export skeletons with redacted config only.
6. Add Whisplay/PiSugar integration behind explicit hardware smoke gates.
