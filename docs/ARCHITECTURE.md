# Architecture

Vibe-Dump is a single Python process. One FastAPI app, one SQLite
database, one event bus, one in-memory agent pipeline. Hardware
bridges (Whisplay, PiSugar, audio capture) are best-effort
attachments that fall back to fakes on a non-Pi dev box; an `rclone`
shim mirrors local data into a configured cloud remote.

## System diagram

```text
                                  ┌────────────────────────┐
                                  │   Browser (mobile /    │
                                  │   desktop dashboard)   │
                                  └────────────┬───────────┘
                                               │ HTTP + SSE
                                               ▼
┌──────────────────────────────────────────────────────────────┐
│                      vibedumpd (one process)                  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │ FastAPI app    │  │ EventBus (SSE) │  │ AgentPipeline  │  │
│  │ (vibedump.app) │◄─┤ bounded deque  │◄─┤ active-listener│  │
│  └───────┬────────┘  └────────┬───────┘  └────────┬───────┘  │
│          │                    │                   │          │
│          ▼                    ▼                   ▼          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │       SQLite (WAL + FTS5, serialized writes)         │    │
│  │   dumps · turns · blueprints · chunks · profile      │    │
│  │   providers · achievements · events_log             │    │
│  └──────────────────────────────────────────────────────┘    │
│          ▲                    ▲                   ▲          │
│          │                    │                   │          │
│  ┌───────┴──────┐  ┌──────────┴──────┐  ┌─────────┴───────┐  │
│  │  STT/LLM/TTS │  │ MascotRenderer  │  │  RagMemory     │  │
│  │  registry    │  │ (PIL frames)    │  │  (chunk + BM25)│  │
│  └──────┬───────┘  └─────────────────┘  └─────────────────┘    │
│         │                                                    │
└─────────┼────────────────────────────────────────────────────┘
          │                       │                    │
          ▼                       ▼                    ▼
   ┌────────────┐         ┌──────────────┐       ┌──────────┐
   │ Whisper /  │         │  Whisplay    │       │ rclone   │
   │ Piper /    │         │  HAT         │       │ ─► Drive │
   │ cloud LLM  │         │  PiSugar 3   │       │  / B2 /  │
   │ (LAN)      │         │  mic + audio │       │  S3      │
   └────────────┘         └──────────────┘       └──────────┘
```

## Module map

| Module | One-line description |
|--------|----------------------|
| `vibedump.app` | FastAPI factory; HTTP routes, SSE, AppState lifetime |
| `vibedump.server` | `uvicorn` entrypoint (`python -m uvicorn vibedump.server:app`) |
| `vibedump.agent_pipeline` | Active-listener loop, blueprint compiler, status transitions |
| `vibedump.active_listener` | LLM response parser (`[ASK]` / `[FINALIZE]`) and prompt renderer |
| `vibedump.database` | SQLite schema, FTS5 search, serialized writes, profile/XP |
| `vibedump.events` | Bounded thread-safe event bus (SSE source) |
| `vibedump.ragmemory` | Chunk + BM25 wrapper over the FTS index |
| `vibedump.schemas` | Blueprint section list, markdown template, validator |
| `vibedump.state` | `DeviceState` enum shared by fakes and real hardware |
| `vibedump.mascot_renderer` | Procedural PIL mascot ("Dumpi") per-state PNG generator |
| `vibedump.provider_selection` | Picks which STT/LLM/TTS names to wire from the registry |
| `vibedump.providers` | Registry factories (`fake_registry`, `build_registry`) and adapters |
| `vibedump.providers.base` | `LLMProvider`, `ProviderRegistry`, `ProviderHealth` Protocol |
| `vibedump.providers.openai_provider` | OpenAI-compatible chat completions client |
| `vibedump.providers.gemini` | Gemini generative-language API client |
| `vibedump.providers.groq` / `minimax` / `nvidia` / `openrouter` | One adapter each, same Protocol |
| `vibedump.providers.local_pc` | LAN Ollama client for the desktop's GPU box |
| `vibedump.providers.whisper_stt` | `faster-whisper` STT adapter with a fake variant |
| `vibedump.providers.piper_tts` | Piper TTS adapter with a fake variant |
| `vibedump.integrations.audio_capture` | ALSA `arecord` push-to-talk capture |
| `vibedump.integrations.whisplay` | SPI LCD + WS2812 LED + button bridge (real + fake) |
| `vibedump.integrations.pisugar` | I2C battery/power telemetry bridge (real + fake) |
| `vibedump.integrations.rclone_sync` | `rclone` CLI wrapper for cloud sync (real + fake) |
| `vibedump.integrations.config_redact` | Provider-config redactor used before every export |
| `vibedump.integrations.backup` | Local tar/zip backups of the data dir |
| `vibedump.integrations.drive_sync` | High-level Drive sync coordinator (wraps `rclone_sync`) |
| `vibedump.static` | Mobile-first dashboard HTML/CSS/JS |
| `scripts.run_dev` | Dev launcher (installs `web` extra on demand) |
| `scripts.install_systemd` | Placeholder for the future systemd unit |
| `scripts.install_whisplay_prereqs` | Idempotent apt + raspi-config + module + group setup |

## Data flow

```text
  mic  ──►  audio_capture  ──►  WAV  ──►  STT (Whisper)  ──►  text
                                                              │
                                                              ▼
                                              database.add_user_turn
                                                              │
                                                              ▼
                              AgentPipeline.step_listener (LLM call)
                                                              │
                                          ┌───────────────────┴─────────────┐
                                          ▼                                 ▼
                                [ASK] follow-up                   [FINALIZE] compile
                                          │                                 │
                                          ▼                                 ▼
                          database.add_assistant_turn         blueprint compiler
                          status: thinking → listening         status: thinking → ready
                                          │                                 │
                                          └──────────► EventBus ◄───────────┘
                                                      │
                                                      ▼
                                              SSE /api/events
                                                      │
                                                      ▼
                                              browser mascot + UI
```

Every transition publishes a `dump.status` event so the dashboard
mascot animates in lockstep with the backend.

## State machine

```text
                 start_dump
        ┌──────────────────────────►
   draft                              listening
        ▲                              │   ▲
        │                  step_listener│   │ step_listener [ASK]
        │                              ▼   │
        │                           thinking
        │                              │
        │              step_listener   │   step_listener
        │              [FINALIZE]      │   [ASK]   (returns to listening)
        │                              ▼
        └─────────────────────────── ready
```

The state lives in `dumps.status` and is mirrored through
`DeviceState` for the mascot and LED. Allowed values:

`draft` → `listening` → `thinking` → `listening` | `ready`

## Milestone status

| ID | Title | Status |
|----|-------|--------|
| M0 | Package skeleton + SQLite + RAG | complete |
| M1 | Fake provider set + one-shot pipeline | complete |
| M2 | FastAPI web surface + dashboard | complete |
| M3 | Provider registry, real LLM adapters | complete |
| M4 | Active-listener pipeline state machine | complete |
| M5 | Mascot frames, XP, achievements | complete |
| M6 | Storage sync (rclone + Drive) + redacted config | complete |
| M7 | Real hardware bridges (Whisplay, PiSugar, audio) | complete |
| M8 | Polish, doc set, soak tests | complete |
| M9 | OpenClaude agent runtime | complete |
| M9.5 | Pydantic-AI backend + tool registry | complete |
| M10 | Self-learning (memory, swarm, skills, evolution) | in progress — see [SWARM_MASTER_ROADMAP.md](SWARM_MASTER_ROADMAP.md) |

## Where M9 (the OpenClaude agent) hooks in

M9 augments today's `AgentPipeline` with a long-lived agent that can
call tools against the same AppState, on top of an event-sourced
conversation state machine. The HTTP routes and `step_listener` contract
remain stable; see `vibedump/agent/` for the runtime wiring.

The intended hook points:

- **`vibedump.agent_pipeline.AgentPipeline`** is the orchestration
  boundary. M9 swaps its body for an agent loop driven by the
  `openlaude` runtime, keeping the public method shapes
  (`start_dump`, `add_user_turn`, `step_listener`, `finalize_dump`)
  stable so the rest of the app and every test does not need to
  change.
- **`vibedump.active_listener`** becomes one of several tools the
  agent can call, alongside `read_dump`, `update_blueprint_section`,
  `search_rag`, and `list_dumps`.
- **`vibedump.schemas.BLUEPRINT_SECTIONS`** stays the canonical
  structure the agent must populate; the validator
  (`validate_blueprint`) remains the gate the agent must pass before
  the dump can move to `ready`.
- **`vibedump.events.EventBus`** is the agent's only outward
  communication channel — every tool the agent invokes still has to
  publish status events, and the SSE stream remains the single
  source of truth for the dashboard.
- **`vibedump.providers.ProviderRegistry`** stays the LLM boundary;
  M9 selects a reasoning model through the same `select_llm_provider`
  helper, and any future tool-calling model is added as another
  `LLMProvider` subclass.

In short: M9 changes the **what** (the agent makes decisions, not
hand-written if/else) but not the **where** (state in SQLite, events
on the bus, transport on FastAPI + SSE).
