# Vibe-Dump

**Voice dumps → software blueprints, on a Pi 5.**

Vibe-Dump is a pocket-sized AI spec goblin for turning chaotic voice
ideas into build-ready software blueprints. You talk into a microphone,
an active-listener LLM asks short clarifying questions, and at the end
you get a structured **Vibe Coding Blueprint** you can drop straight
into Cursor, Claude Code, Codex, or Gemini. The whole thing runs as a
single Python process on a Raspberry Pi 5 wearing a Whisplay HAT, a
PiSugar 3 battery, and a microphone — and the dashboard also works
from your phone on the same LAN.

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/<placeholder>/vibe-dump/main/scripts/install.sh | bash
```

> The URL above is a placeholder until the repo goes public. See
> [docs/INSTALL.md](docs/INSTALL.md) for the manual install path.

## Status

> Milestones 0–7 complete. M8 polish in progress. M9 agent runtime planned.

## Screenshots

| | |
|---|---|
| ![Dumpi idle](docs/screenshots/dumpi-idle.png) | ![Dumpi listening](docs/screenshots/dumpi-listening.png) |
| ![Dumpi blueprint](docs/screenshots/dumpi-blueprint.png) | ![Dashboard mobile](docs/screenshots/dashboard-mobile.png) |
| ![Dashboard settings](docs/screenshots/dashboard-settings.png) | ![Storage panel](docs/screenshots/storage-panel.png) |

## Features

- **Voice capture** — push-to-talk recording via USB or 3.5 mm microphone
  on the Pi, exposed to the agent pipeline as in-process WAVs.
- **Real Whisper STT** — `faster-whisper` adapter, with a `fake`
  fallback for tests and zero-config dev.
- **Real Piper TTS** — local neural TTS for audible readback of the
  listener's clarifying questions.
- **7 LLM providers** — OpenAI, OpenRouter, NVIDIA NIM, Groq, MiniMax,
  Gemini, and a `local_pc` adapter that talks to the desktop's
  Ollama. Missing keys silently drop a provider; the dashboard
  surfaces the reason in the provider health list.
- **SQLite RAG** — FTS5-backed chunk store, BM25 search, chunked
  transcripts and blueprints reused across dumps.
- **Mascot frames** — procedural PIL renderer for **Dumpi**, one
  palette per `DeviceState` (idle, listening, thinking, speaking,
  error, level-up, sleeping, draft, ready).
- **XP / achievements** — per-profile XP curve, level-ups trigger
  `level_up` mascot frames, and the achievements table tracks
  unlocks surfaced in the dashboard.
- **Whisplay HAT** — SPI-driven 240×280 LCD, WS2812 LED, and four
  buttons (A/B/C/D) for physical push-to-talk.
- **PiSugar telemetry** — battery / voltage / current / temperature
  polled over I2C and streamed to the dashboard as
  `hardware.pisugar.reading` events.
- **rclone + Drive sync** — one-way mirror of `dumps/`, `blueprints/`,
  `audio/`, and `exports/` to a configured rclone remote
  (Google Drive by default), with the provider config always sent
  through the redactor first.

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

## Quick start (manual)

```bash
# 1. Clone and enter the repo
git clone https://github.com/<placeholder>/vibe-dump.git
cd vibe-dump

# 2. Install Python deps (the web extra pulls FastAPI + uvicorn)
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"

# 3. Run the dev server (defaults to :8080, mobile-first dashboard)
./scripts/run_dev.sh
```

The dashboard listens on `0.0.0.0:8080`. Open it from your phone on
the same LAN to see the live SSE feed and mascot updates.

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
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `NVIDIA_API_KEY` / `GROQ_API_KEY` / `MINIMAX_API_KEY` / `GEMINI_API_KEY` | _(unset)_ | Cloud LLM credentials; any subset may be set |

> No secrets are ever committed. The committed `.env.example` and
> `config.example.json` are placeholders — copy and edit locally.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system diagram,
  module map, data flow, state machine, milestone status.
- [docs/HARDWARE.md](docs/HARDWARE.md) — Whisplay pin map, PiSugar I2C
  address, mic/speaker notes, assembly photos, power budget.
- [docs/INSTALL.md](docs/INSTALL.md) — one-liner, manual install,
  verification, update, uninstall.

## License

MIT. See [LICENSE](LICENSE).
