# Vibe-Dump

> Voice dumps into dev-ready software blueprints. Pocket-sized, runs on a Pi Zero 2 W, polite enough for any Linux or macOS box.

[![CI](https://github.com/NaustudentX18/vibe-dump/actions/workflows/ci.yml/badge.svg)](https://github.com/NaustudentX18/vibe-dump/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Release v0.2.0](https://img.shields.io/badge/Release-v0.2.0-FF4081.svg)](docs/RELEASE_v0.2.0.md)

---

## What it is

Vibe-Dump is a pocket-sized voice-to-blueprint compiler. Speak a rough idea into it, get a 12-section software spec back, ready to drop into Cursor, Claude Code, or any agent-friendly IDE. The mascot, Dumpi, watches the active-listener state machine animate from `idle` to `ready` while your thought becomes a plan.

The canonical target is a Raspberry Pi Zero 2 W wearing a PiSugar Whisplay HAT (LCD, buttons, dual MEMS mics, onboard speaker) and a PiSugar 3 battery HAT, running a single Python process you can reach from your phone. Drop the HATs and the same code happily runs on any Linux or macOS dev box in **fake mode** — no API keys, no cloud, no hardware, just the dashboard.

---

## 30-second demo

> Demo GIF coming soon — record a voice-dump to blueprint flow to replace this.

**Try it in 30 seconds:**

```bash
git clone https://github.com/NaustudentX18/vibe-dump.git
cd vibe-dump
pip install -e ".[web]"
./scripts/run_dev.sh
# open http://localhost:8080
```

---

## Screenshots

The full screenshot set is being rebuilt for the v0.2.0 ship. For
now, here's what we have — more will land as `docs/screenshots/` is
re-shot against the second-wave UI.

| Pi hardware build | Dumpi listening |
| :---: | :---: |
| ![Pi Zero 2 W with the Whisplay HAT stacked on top](docs/screenshots/assembly-1.jpg) | ![Dumpi listening mascot animating with the mic active](docs/screenshots/dumpi-listening.png) |

> See `docs/screenshots/` for the raw source images, and
> [`docs/HARDWARE.md`](docs/HARDWARE.md) for the assembly walkthrough
> and pin map.

---

## How a vibe-dump flows

Vibe-Dump coordinates hardware buttons, audio capture, the active-listener state machine, and a swarm of LLM nodes to turn voice transcripts into markdown blueprints.

```
[Speech] -> Mic Capture -> STT (Whisper / fake) -> AgentPipeline
                                                -> [ASK]? Clarify : [FINALIZE] Compile
                                                -> Blueprint Compiler
                                                -> rclone -> Drive / S3 / B2
```

The active-listener workflow runs through a single state list:

`draft` -> `listening` -> `thinking` -> `listening` | `ready`

Every transition publishes a `dump.status` event to the SSE event bus, which animates the Dumpi mascot on the Whisplay screen and the mobile dashboard in lockstep.

---

## Features

### Capture
- Web mic push-to-talk in the mobile dashboard
- USB or Bluetooth audio input as a drop-in upgrade
- Whisplay HAT MEMS mics (WM8960) for the Pi build
- Physical push-to-talk via the Whisplay Button D

### Intelligence
- Active-listener state machine with explicit `[ASK]` and `[FINALIZE]` turns
- Pydantic-graph agent swarm with Architect, Critic, and Security nodes
- 7 LLM providers: OpenAI, OpenRouter, NVIDIA NIM, Groq, Anthropic, Gemini, plus local Ollama
- Zero-config fake mode that boots without a single API key
- Whisper STT and Piper TTS adapters, each with a fake fallback

### Storage & Sync
- SQLite with FTS5-powered RAG over chunked transcripts
- rclone sync to Google Drive, S3, or Backblaze B2
- Atomic write-verify for every export and blueprint
- Redacted provider config export for safe sharing

### Experience
- Dumpi mascot rendered as 8 procedural PIL frames per state
- XP, levels, and achievements logged locally
- Mobile-first dashboard with chat bubbles, dump filters, and delete sheets
- Server-Sent Events event bus for real-time UI updates

---

## Architecture

One Python process, one FastAPI app, one SQLite database, one in-memory agent pipeline, one bounded event bus. Hardware bridges (Whisplay, PiSugar, audio capture) are best-effort attachments that fall back to fakes on any non-Pi dev box. The system sits in five layers:

```
            Browser (mobile / desktop)
                     |
                     v
   +---+   +-----------+   +----------------+
   |   |   |           |   |                |
   |   |   |  EventBus |   |  AgentPipeline |
   |   |   |   (SSE)   |   | active-listener|
   |   |   +-----+-----+   +--------+-------+
   |   |         |                  |
   |   v         v                  v
   |  FastAPI  <-- SQLite (WAL + FTS5, serialized writes) --+
   |   app                                              |
   +---+------------------------------------------------+
         |        |                 |          |
         v        v                 v          v
     STT/LLM/TTS  Mascot         RAG        rclone
     registry     Renderer      memory   -> Drive / S3
```

Full deep-dive, including the module map and the data-flow diagram, lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Hardware BOM

Full physical details, pin map, and I2C addresses in [`docs/HARDWARE.md`](docs/HARDWARE.md).

| Qty | Component | Role | Specs & Notes |
| :---: | :--- | :--- | :--- |
| **1** | Raspberry Pi Zero 2 W | Compute Engine | 512 MB RAM, headless. |
| **1** | PiSugar Whisplay HAT | UI + Audio | 240x280 LCD, 4 buttons, WS2812 LED, WM8960 dual MEMS mics + onboard speaker. |
| **1** | PiSugar 3 Battery HAT | Power & Telemetry | I2C battery / voltage / current readings, UPS mode. |
| **0** | USB / BT speaker (optional) | Louder TTS | 3.5 mm jack or USB/BT if the onboard speaker is not loud enough. |

---

## Quickstart

### Manual install

Requires Python 3.11+. The web extra pulls in FastAPI and Uvicorn; that's all you need to run the dashboard.

```bash
# 1. Clone
git clone https://github.com/NaustudentX18/vibe-dump.git
cd vibe-dump

# 2. Set up a venv
python -m venv .venv
source .venv/bin/activate

# 3. Install with the web dashboard
pip install -e ".[web]"

# 4. Run
./scripts/run_dev.sh
# dashboard: http://localhost:8080
```

**Zero-config dev:** `pip install -e ".[web]"` works against the fake provider set with no API keys needed. Boot it, hit the dashboard, watch Dumpi cycle through states on canned transcripts.

### Real providers

To swap the fakes for real STT, LLM, and TTS, copy the example env and add at least one key:

```bash
cp .env.example .env
# edit .env -- set OPENAI_API_KEY (or whichever provider you want)
# set VIBEDUMP_REGISTRY=real
./scripts/run_dev.sh
```

Any provider with a key present is registered; any with a missing key is silently omitted and surfaced on the dashboard's provider health list. Add `VIBEDUMP_AGENT=openlaude` to flip on the Pydantic-AI swarm runtime (M9.5). The systemd unit and Whisplay HAT prereqs are Pi-only extras; see [`docs/INSTALL.md`](docs/INSTALL.md) for the full hardware path.

---

## Configuration

All integrations degrade to fakes if the relevant variable is omitted. Copy `.env.example` to `.env` and edit locally; do not commit it.

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `VIBEDUMP_REGISTRY` | `fake` | Provider mode. `real` enables cloud LLM / STT / TTS backends. |
| `VIBEDUMP_STT_PROVIDER` | `fake` | Speech-to-text backend. `fake` or `whisper`. |
| `VIBEDUMP_LLM_PROVIDER` | `fake` | Active-listener backend. `openai`, `groq`, `claude`, `gemini`, `local_pc`, more. |
| `VIBEDUMP_TTS_PROVIDER` | `fake` | Text-to-speech readback. `fake` or `piper`. |
| `VIBEDUMP_AGENT` | `inline` | Agent runtime. `inline` (always-on) or `openlaude` (Pydantic-AI swarm). |
| `VIBEDUMP_PC_BASE_URL` | `http://desktop-ujsii52.local:11434` | Ollama endpoint on the companion desktop PC. |
| `VIBEDUMP_PC_MODEL` | `qwen3-14b-agent` | Companion Ollama model used. |
| `VIBEDUMP_RCLONE_REMOTE` | `gdrive:` | rclone target for cloud drive backup uploads. |
| `VIBEDUMP_ALSA_CAPTURE_DEVICE` | *(auto)* | ALSA device for `arecord` (Whisplay WM8960). |
| `VIBEDUMP_ALSA_PLAYBACK_DEVICE` | *(auto)* | ALSA device for `aplay` TTS playback. |

Provider-specific notes live in [`docs/PROVIDERS.md`](docs/PROVIDERS.md).

---

## Testing & CI

```bash
# Run the full suite (517 tests as of v0.2.0)
python -m pytest -q

# Lint
ruff check vibedump tests
```

GitHub Actions runs both on every push to `main` — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml). The CI badge at the top of this README is the live status.

---

## Roadmap

Shipped in **v0.2.0**: Whisplay WM8960 audio path, dashboard split into static assets, markdown blueprints, push-to-talk UX, M10 memory store and swarm DAG scaffolding, light theme, chat bubbles, achievement overlay, 517 tests with GitHub Actions CI. See [`docs/RELEASE_v0.2.0.md`](docs/RELEASE_v0.2.0.md) for the full milestone log.

Next: real-time audio via Pipecat / WebRTC, Model Context Protocol host, the multi-agent Vibe Swarm (Architect + Critic + Security), and IDE companion endpoints for Cursor and Claude Code. Full picture in [`docs/V2_ROADMAP.md`](docs/V2_ROADMAP.md).

---

## Contributing

Issues, PRs, and Dumpi-themed bug reports welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the workflow, the issue templates for bug and feature reports, the pull request template, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for the ground rules.

---

## Acknowledgements

- **PiSugar** for the Whisplay HAT and PiSugar 3 battery board that make the pocket build real
- **Pydantic-AI** for the graph-backed agent runtime that powers the swarm
- **FastAPI** for the HTTP, SSE, and WebSocket plumbing underneath the dashboard
- **Contributor Covenant** for the code of conduct template

---

## License

MIT — see [`LICENSE`](LICENSE).

<sub>Dumpi says: speak the vibe, ship the spec.</sub>
