# Vibe-Dump

> **Voice dumps → software blueprints, on a Pi 5.**

Vibe-Dump is a pocket-sized AI spec goblin for turning chaotic voice
ideas into build-ready **Vibe Coding Blueprints** you can drop straight
into Cursor, Claude Code, Codex, or Gemini. Talk into a microphone; the
active-listener LLM asks short clarifying questions; at the end you
get a structured markdown blueprint in your hand. The whole thing
runs as a single Python process on a Raspberry Pi 5 wearing a
Whisplay HAT, a PiSugar 3 battery, and a USB microphone — and the
mobile-first dashboard also works from your phone on the same LAN.

[![Tests](https://img.shields.io/badge/tests-430%20passed-2ea043)](#testing)
[![Python](https://img.shields.io/badge/python-3.13-3776ab)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-3da639)](LICENSE)
[![Status](https://img.shields.io/badge/M9.5-shipped-7d4cdb)](docs/M9.5_EOD_HANDOVER.md)
[![Mascot](https://img.shields.io/badge/mascot-Dumpi-ff79c6)](#mascot)

---

## What it looks like

| | |
|---|---|
| ![Dumpi idle](docs/screenshots/dumpi-idle.png) | ![Dumpi listening](docs/screenshots/dumpi-listening.png) |
| ![Dumpi blueprint](docs/screenshots/dumpi-blueprint.png) | ![Dashboard mobile](docs/screenshots/dashboard-mobile.png) |
| ![Dashboard settings](docs/screenshots/dashboard-settings.png) | ![Storage panel](docs/screenshots/storage-panel.png) |
| ![Assembly 1](docs/screenshots/assembly-1.jpg) | ![Assembly 2](docs/screenshots/assembly-2.jpg) |

> Replace the placeholder PNGs in `docs/screenshots/` with real captures
> from your own Pi. The file names are stable so the gallery keeps
> rendering.

---

## Quickstart

```bash
# One-liner (canonical placeholder; replace with the real upstream URL)
curl -fsSL https://raw.githubusercontent.com/<placeholder>/vibe-dump/main/scripts/install.sh | bash

# Or the live public repo:
curl -fsSL https://raw.githubusercontent.com/NaustudentX18/vibe-dump/main/scripts/install.sh | bash
```

### Manual install

```bash
git clone https://github.com/forestwelch/vibe-dump.git
cd vibe-dump
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web,all]"        # all = web + dev + agent extras
./scripts/run_dev.sh
```

The dashboard listens on `0.0.0.0:8080` and is mobile-first — open it
from your phone on the same LAN to see live SSE updates.

---

## How a dump flows

```
mic ──► STT (faster-whisper)  ──►  AgentPipeline (active-listener)
                                       │
                                       ▼
                              [ASK] What platform?
                              [FINALIZE] we have enough
                                       │
                                       ▼
                              Blueprint Compiler (LLM)
                                       │
                                       ▼
                            Vibe Coding Blueprint (markdown)
                                       │
                                       ▼
                              rclone ──► Google Drive
```

The active-listener loop is the M4 backbone: one `[ASK]` / `[FINALIZE]`
directive per LLM turn, persisted as a turn on the dump, status
transitions published on the bus (`dump.status`) so the dashboard
mascot animates in real time. M9.5 wraps the same flow in a
[pydantic-graph](https://github.com/pydantic/pydantic-graph) state
machine with SQLite-backed snapshot persistence.

---

## Features

### Capture & providers
- **Voice capture** — push-to-talk recording via USB or 3.5 mm mic,
  exposed to the agent pipeline as in-process WAVs. Works from the
  web UI, the Pi HAT buttons, or the mobile dashboard.
- **`faster-whisper` STT** with a `fake` fallback for tests and
  zero-config dev.
- **Piper TTS** for audible readback of the listener's clarifying
  questions on the Pi.
- **7 LLM providers** — OpenAI, OpenRouter, NVIDIA NIM, Groq, Claude,
  Gemini, and a `local_pc` adapter that talks to the desktop's
  Ollama. Missing keys silently drop a provider; the dashboard
  surfaces the reason in the provider health list.

### Agent runtime (M9.5)
- **`OpenClaude`** — an inline tool-use loop in pure Python. The
  canonical path; works against any chat-capable LLM.
- **`PydanticAIBackend`** — when `pydantic-ai` is installed, a thin
  wrapper exposes the same `run()` shape with the OpenAI-compatible
  provider pointed at Ollama.
- **`GraphPipeline`** — pydantic-graph state machine on top of
  `DraftNode` → `ListeningNode` → `ThinkingNode`. The graph snapshot
  is persisted to `dumps.metadata_json` so a run can resume from an
  intermediate node.
- **`agent.trace` SSE** — every agent run publishes a structured
  trace event (steps, duration, model, tool calls, final preview) to
  the bus. The dashboard renders the last 50 runs in a collapsible
  panel.
- **Structured output** — the listener's `[ASK] / [FINALIZE]`
  decision is a frozen Pydantic `ListenerDecision`. The
  `parse_listener_response_structured()` entry point tries
  `instructor` first and falls back to the regex parser when
  instructor is missing.

### Data & sync
- **SQLite RAG** — FTS5-backed chunk store, BM25 search, chunked
  transcripts and blueprints reused across dumps.
- **rclone + Drive sync** — one-way mirror of `dumps/`, `blueprints/`,
  `audio/`, and `exports/` to a configured rclone remote (Google
  Drive by default), with the provider config always sent through
  the redactor first.
- **Bundled export** — one-shot `zip` of a dump + blueprint + raw
  audio for handoff to a coding agent.

### UI & mascot
- **Mobile-first dashboard** — dark theme, real-time SSE feed,
  speech bubble chat, achievement toast.
- **Dumpi mascot** — procedural PIL renderer, one palette per
  `DeviceState` (idle, listening, thinking, speaking, error,
  level-up, sleeping, draft, ready).
- **XP / achievements** — per-profile XP curve, level-ups trigger
  `level_up` mascot frames, and the achievements table tracks
  unlocks surfaced in the dashboard.

### Hardware integration
- **Whisplay HAT** — SPI-driven 240×280 LCD, WS2812 LED, and four
  buttons (A/B/C/D) for physical push-to-talk.
- **PiSugar telemetry** — battery / voltage / current / temperature
  polled over I2C and streamed to the dashboard as
  `hardware.pisugar.reading` events.

---

## Hardware BOM

The canonical physical target. See [docs/HARDWARE.md](docs/HARDWARE.md)
for the pin map and assembly photos.

| Qty | Item | Notes |
|----:|------|-------|
| 1 | Raspberry Pi 5 (8 GB+) | 4 GB is too tight once Whisper + the web worker run together |
| 1 | Waveshare Whisplay HAT | 240×280 ST7789 LCD + 4 buttons + WS2812 LED |
| 1 | PiSugar 3 battery HAT | I2C telemetry, 5 V boost, optional UPS |
| 1 | USB or 3.5 mm microphone | any ALSA-visible input works |
| 1 | Speaker | 3.5 mm jack or the Whisplay's built-in piezo path |

Power: a 5 V / 3 A USB-C supply is recommended for worst-case Whisper
+ Wi-Fi draws.

---

## Configuration

All config is read from environment variables; an example file lives
at [.env.example](.env.example). Copy it to `.env` and fill in only
what you need — every LLM key is optional.

| Variable | Default | Purpose |
|----------|---------|---------|
| `VIBEDUMP_REGISTRY` | `fake` | `fake` for zero-config dev / tests; `real` for the production provider set |
| `VIBEDUMP_STT_PROVIDER` | `fake` | `fake` or `whisper` |
| `VIBEDUMP_LLM_PROVIDER` | `fake` | One of the registered LLM names (e.g. `openai`, `groq`, `local_pc`) |
| `VIBEDUMP_TTS_PROVIDER` | `fake` | `fake` or `piper` |
| `VIBEDUMP_DATABASE_PATH` | `data/vibedump.sqlite3` | SQLite path; honoured when set |
| `VIBEDUMP_PC_BASE_URL` | `http://desktop-ujsii52.local:11434` | Ollama base URL for the `local_pc` adapter |
| `VIBEDUMP_PC_MODEL` | `qwen3-14b-agent` | Default model the `local_pc` adapter requests |
| `VIBEDUMP_RCLONE_REMOTE` | `gdrive:` | Default rclone remote for the storage sync |
| `VIBEDUMP_PTT_DIR` | `/tmp` | Where push-to-talk WAVs are written |
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `NVIDIA_API_KEY` / `GROQ_API_KEY` / `CLAUDE_API_KEY` / `GEMINI_API_KEY` | _(unset)_ | Cloud LLM credentials; any subset may be set |

> No secrets are ever committed. The committed `.env.example` and
> `config.example.json` are placeholders — copy and edit locally.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system diagram,
  module map, data flow, state machine, milestone status.
- [docs/HARDWARE.md](docs/HARDWARE.md) — Whisplay pin map, PiSugar I2C
  address, mic/speaker notes, assembly photos, power budget.
- [docs/INSTALL.md](docs/INSTALL.md) — one-liner, manual install,
  verification, update, uninstall.
- [docs/M9.5_EOD_HANDOVER.md](docs/M9.5_EOD_HANDOVER.md) — what
  shipped in the M9.5 openlaude runtime upgrade (pydantic-ai +
  pydantic-graph + instructor).
- [docs/BLUEPRINT_SCHEMA.md](docs/BLUEPRINT_SCHEMA.md) — the
  Vibe Coding Blueprint section contract that the compiler fills in.
- [docs/PROVIDERS.md](docs/PROVIDERS.md) — adding a new provider.
- [docs/DRIVE_SYNC.md](docs/DRIVE_SYNC.md) — rclone remote setup.
- [docs/LOCAL_MODELS.md](docs/LOCAL_MODELS.md) — Ollama model
  catalog and switching.

---

## Testing

```bash
source .venv/bin/activate
python -m pytest -q        # 430 tests, ~30s
```

Coverage is concentrated on the agent runtime, listener parser, tool
registry, and the active-listener pipeline. Pyright diagnostics are
info-only at the swarm stage (intentional — see M9.5 rule 4 in
`docs/M9.5_EOD_HANDOVER.md`); the pre-commit secret hook is what
keeps the tree clean.

The agent tests use a real `EventBus` and a real `Database` (in
`:memory:`); only the LLM calls are stubbed. End-to-end against a
live Ollama needs a host on the LAN running the desktop models
catalog (see `docs/LOCAL_MODELS.md`).

---

## Mascot

Dumpi is rendered procedurally by `vibedump/mascot.py` with one
palette per `DeviceState`. The mascot frames are cached on disk so
the dashboard loads instantly; the cache invalidates when the
renderer code changes.

| State | When | Frame |
|-------|------|-------|
| `idle` | default | neutral pose, default palette |
| `listening` | dump in LISTENING | ear perked, mic icon |
| `thinking` | dump in THINKING | thought bubble |
| `ready` | blueprint ready | check-mark, gold accent |
| `level_up` | XP level boundary | confetti |
| `error` | LLM or pipeline error | red X, defensive posture |

---

## Project status

M0–M9.5 complete. M9.5 wraps the agent runtime on pydantic-ai +
pydantic-graph + instructor while keeping the inline tool-use loop
as the canonical fallback. See the M9.5 EOD handover for the full
delta. M10 (next milestone) is a multi-dump batch import + Drive
two-way sync.

---

## License

MIT. See [LICENSE](LICENSE).
