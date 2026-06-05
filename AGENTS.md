# AGENTS.md

## Cursor Cloud specific instructions

### Product

Vibe-Dump is a single-process Python FastAPI app: voice-dump → AI spec compiler with a mobile web dashboard. Default dev mode uses **fake** STT/LLM/TTS providers (no API keys).

### Services (dev)

| Service | Required? | Start |
|---------|-----------|-------|
| Vibe-Dump web server | Yes | `./scripts/run_dev.sh` (port **8080**) |
| SQLite | Bundled | Auto-created at `data/vibedump.sqlite3` on startup |
| Pi hardware (Whisplay, PiSugar) | No | Only on Raspberry Pi |

### Standard commands

See `README.md` and `docs/INSTALL.md` for canonical setup. Quick reference:

```bash
source .venv/bin/activate
python -m pytest          # 512+ tests; install Pillow for mascot suite
python -m ruff check vibedump tests   # ruff not pinned in pyproject; install separately
./scripts/run_dev.sh      # dashboard at http://127.0.0.1:8080
```

Health probes: `curl http://127.0.0.1:8080/api/health` and `curl http://127.0.0.1:8080/api/profile`.

### Gotchas

- **`python3-venv`**: On fresh Ubuntu/Debian, `python3 -m venv` fails until `python3.12-venv` is installed (system package, not in update script).
- **Pillow**: Optional at runtime but **required** for mascot renderer tests (`pip install pillow`). Without it, 17 mascot tests fail; core app still runs.
- **`/api/dumps/{id}/turn`**: May return 502 with fake LLM when the listener emits a blueprint directly instead of `[ASK]`/`[FINALIZE]` prefixes. Use `/api/dumps/{id}/ingest-fake` for a reliable fake-pipeline smoke test.
- **Config**: Copy `.env.example` → `.env` (defaults to `VIBEDUMP_REGISTRY=fake`). Do not commit `.env`.
- **Ruff**: Configured in `pyproject.toml` but not listed as a dev dependency; the repo currently has pre-existing lint findings.
