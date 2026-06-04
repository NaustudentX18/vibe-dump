# Vibe-Dump

> A pocket AI spec goblin for turning chaotic voice ideas into build-ready software blueprints.

Vibe-Dump runs on a Raspberry Pi Zero 2 W + PiSugar 3 + Whisplay HAT. You talk, it captures, an active-listener LLM asks short clarifying questions, and at the end you get a structured **Vibe Coding Blueprint** ready to drop into Cursor, Claude, Codex, or Gemini.

This repo currently implements the **Milestone 2 fake-first scaffold** (web dashboard + JSON API) layered on top of the Milestone 0/1 database / RAG / fake pipeline foundation.

## Current status

### Implemented

- Python package under `vibedump/`.
- Config examples: `.env.example` and `config.example.json`.
- Fake hardware, mascot, STT, LLM, and TTS components (no real Whisplay/PiSugar touch yet).
- SQLite persistence with WAL, FK enforcement, serialized writes, FTS5 search, and dump/turn/blueprint/chunk/provider/event/profile tables.
- `RagMemory` chunking + BM25 search.
- Fake pipeline that stores transcript, blueprint, and searchable memory.
- **FastAPI web surface** (`vibedump.app.create_app`) with:
  - `GET /` mobile-first dashboard with hamburger drawer, settings drawer, bottom action bar, mascot state chip, live SSE updates.
  - `GET /api/health`, `GET/POST /api/dumps`, `GET/PATCH/DELETE /api/dumps/{id}`, `GET /api/dumps/{id}/turns`, `GET /api/dumps/{id}/blueprint`, `POST /api/dumps/{id}/ingest-fake`, `PATCH /api/dumps/{id}/status`.
  - `GET /api/search`, `GET /api/rag/memory` (BM25 over FTS).
  - `GET/POST /api/providers` (registry health + per-provider config upsert).
  - `GET /api/events` (SSE — streams the current event bus snapshot; clients reconnect via `EventSource`).
- Tests for database CRUD, RAG memory, fake providers, fake pipeline, XP profile seed, and the full FastAPI surface (41 passing).
- Real LLM provider adapters behind `VIBEDUMP_REGISTRY=real`:
  OpenAI, OpenRouter, NVIDIA NIM, Groq, MiniMax (OpenAI-compatible chat
  completions), Gemini (generative language API), and `local_pc` for the
  desktop's Ollama. Stdlib `urllib` only - no extra HTTP dep on the Pi.

### Not yet

- Real Whisplay daemon socket, PiSugar battery polling, real audio record/playback.
- Real STT / TTS adapters (only the `fake` STT and TTS are wired; real LLMs
  are usable today).
- PIL mascot frames and XP / achievements UI.
- Google Drive / rclone sync, exports, redacted config.
- Systemd unit beyond the placeholder.

## Architecture

The handover in `/home/pi/VIBE_DUMP_BUILD_HANDOVER_2026-06-04.md` is the source of truth. Short version:

```text
vibedumpd: single Python process
├── FastAPI on :8080 (one worker)
├── AppState: { db, bus, pipeline, memory, device_state }
│   ├── Database (SQLite WAL + FTS5, serialized writes)
│   ├── EventBus (bounded deque, SSE)
│   ├── FakeAgentPipeline (fake STT/LLM/TTS)
│   └── RagMemory (chunk + BM25)
└── mobile-first dashboard at /
```

512MB RAM is the binding constraint: one process, one web worker, no background threads that aren't strictly necessary. The SSE endpoint streams a snapshot and lets the browser reconnect, which keeps the server loop trivial.

## Quick start (development)

```bash
cd /home/pi/vibe-dump
python -m pytest -q            # 21 passed
./scripts/run_dev.sh           # http://0.0.0.0:8080
```

`run_dev.sh` installs the `web` extra on first run if FastAPI/uvicorn are missing. The dashboard is mobile-first; open it on a phone on the same LAN.

## HTTP API

| Method | Path                              | Purpose                                                |
|--------|-----------------------------------|--------------------------------------------------------|
| GET    | `/`                               | Mobile dashboard (HTML)                                |
| GET    | `/api/health`                     | Liveness probe                                         |
| GET    | `/api/dumps?limit&offset`         | List dumps (newest first)                              |
| POST   | `/api/dumps`                      | Create a dump (optional `audio_path` seeds a turn)     |
| GET    | `/api/dumps/{id}`                 | Single dump                                            |
| DELETE | `/api/dumps/{id}`                 | Delete (cascades turns/blueprints/chunks)              |
| GET    | `/api/dumps/{id}/turns`           | Transcript turns                                       |
| GET    | `/api/dumps/{id}/blueprint`       | Latest blueprint markdown                              |
| POST   | `/api/dumps/{id}/ingest-fake`     | Run the fake pipeline on an existing dump              |
| PATCH  | `/api/dumps/{id}/status`          | Update device/dump status (drives the mascot)          |
| GET    | `/api/search?q=...&limit=...`     | BM25 search over FTS chunks                            |
| GET    | `/api/rag/memory?q=...`           | Alias of `/api/search`                                 |
| GET    | `/api/providers`                  | Registry health + stored provider configs              |
| POST   | `/api/providers`                  | Upsert a provider config (`name`, `kind`, `config`)    |
| GET    | `/api/events`                     | SSE: replays the event bus snapshot                    |

Event types emitted today: `dump.created`, `dump.deleted`, `blueprint.generated`, `provider.updated`, `dump.status`.

## Configuration

- `.env` (gitignored) — provider API keys, base URLs.
- `config.example.json` — typed config defaults; copy to `config.json` for a persistent DB path.
- `VIBEDUMP_REGISTRY=fake` (default) wires the zero-config fake provider set.
  `VIBEDUMP_REGISTRY=real` wires `build_registry()`: any cloud LLM with a key
  set is registered (OpenAI, OpenRouter, NVIDIA, Groq, MiniMax, Gemini) plus
  the LAN `local_pc` Ollama adapter. Missing keys -> provider omitted (no
  crash); the dashboard surfaces the reason in the provider health list.
- The 512MB Pi Zero 2 W is the target, so the runtime deps stay minimal:
  `fastapi`, `uvicorn`, and the standard library (no `httpx`/`requests`).

## Project layout

```text
vibe-dump/
├── pyproject.toml
├── README.md
├── .env.example
├── config.example.json
├── vibedump/
│   ├── __init__.py
│   ├── config.py
│   ├── app.py              ← FastAPI factory + routes
│   ├── server.py           ← uvicorn entrypoint
│   ├── events.py
│   ├── state.py
│   ├── hardware_control.py
│   ├── mascot_renderer.py
│   ├── agent_pipeline.py
│   ├── ragmemory.py
│   ├── database.py
│   ├── schemas.py
│   ├── providers/
│   ├── integrations/
│   └── static/dashboard.html
├── scripts/
│   ├── run_dev.sh
│   ├── install_systemd.sh
│   ├── install_whisplay_prereqs.sh
│   └── hardware_smoke.sh
└── tests/
    ├── test_database.py
    ├── test_ragmemory.py
    ├── test_agent_pipeline.py
    ├── test_provider_router.py
    ├── test_xp.py
    ├── test_server.py
    └── fakes/
```

## Next milestones

1. Full agent pipeline state machine (active listener LLM, finalize command, blueprint compiler with real model).
2. Real STT / TTS adapters (Whisper / Groq Whisper, Piper / ElevenLabs).
3. PIL mascot frames + XP/achievements UI.
4. Storage sync (rclone) with redacted config only.
5. Whisplay daemon socket, button events, LED, LCD framebuffer; PiSugar battery poller; audio record/playback.
6. Systemd unit, soak tests, real-hardware verification gates (no destructive commands without explicit user approval).

## License

TBD.
