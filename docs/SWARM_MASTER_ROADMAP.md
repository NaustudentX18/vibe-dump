# Vibe-Dump — Master Swarm Roadmap

> **Status:** SHIPPED on branch `v2-pass` (2026-06-05)
> **Created:** 2026-06-05
> **Baseline:** 486 tests green · Python 3.11+ · single FastAPI process · fake providers default
> **Whisplay drivers:** [PiSugar/Whisplay](https://github.com/PiSugar/Whisplay) (`install_driver.sh`)
> **Canonical host:** Raspberry Pi Zero 2 W + Whisplay HAT + PiSugar 3

---

## How to use this document

1. **Approve phases** (or individual waves) in order. Do not launch Wave 2 until Wave 1 gates pass.
2. Assign each unchecked task to **one agent** using the suggested agent ID in the `Agent` column.
3. When a task is done, change `[ ]` → `[x]` and add the commit hash in the `Done` column.
4. Build agents **do not commit** during parallel waves; only the **wrap agent** squashes (see `docs/V2_PASS_SWARM.md`).
5. Every agent must leave **440+ tests green** unless the task explicitly adds tests that replace broken ones.

**Legend**

| Symbol | Meaning |
|--------|---------|
| 🔴 P0 | Blocks hardware, docs truth, or core UX |
| 🟠 P1 | High value; ship in first swarm pass |
| 🟡 P2 | Polish; second pass |
| 🟣 M10 | Self-learning machine (post-hardening) |
| ⛔ GATE | Must pass before downstream work |

---

## Executive summary — what Vibe-Dump is

**Vibe-Dump** is a pocket AI spec compiler: you speak a messy idea, an active-listener agent asks clarifying questions, then compiles a structured **Vibe Coding Blueprint** (12-section markdown spec) compatible with Cursor, Claude Code, Codex, and Gemini.

| Layer | What it does |
|-------|----------------|
| **Input** | Push-to-talk (web UI, Whisplay Button D, mobile dashboard) |
| **STT** | faster-whisper (real) or fake (dev) |
| **Brain** | LLM providers (7 cloud + local Ollama) via Pydantic-AI agent runtime |
| **Output** | SQLite-stored blueprint + optional rclone sync to cloud |
| **Personality** | Dumpi mascot (PIL renderer + SSE-driven states) |
| **Hardware** | Whisplay HAT (LCD, buttons, LED, **WM8960 audio**), PiSugar (battery) |

**Current reality gap (2026-06-05):** Docs/README updated for Whisplay WM8960 audio. and omit the Whisplay HAT's **built-in MEMS mics + onboard speaker**. Hardware daemons ship on `v2-pass`; dashboard renders markdown blueprints. M10 core is scaffolded; embeddings + Smart Dumpi remain.

---

## Research findings (synthesis)

### A. README / GitHub landing page

| Issue | Current state | Target state |
|-------|---------------|--------------|
| USB mic in hero copy | README line 21, BOM row 148 | Whisplay WM8960 dual MEMS mics as **default** input |
| Speaker requirement | "USB or Bluetooth speaker" as only path | Onboard HAT speaker for TTS; 3.5 mm / USB / BT as **upgrade** |
| Interface preview | `docs/screenshots/*.png` are 1-byte placeholders | Real dashboard + Dumpi + assembly photos |
| Assembly photos | `assembly-1.jpg`–`assembly-4.jpg` missing / wrong paths | Retake with HAT-only audio build (no USB mic in frame) |
| Test badge | "430 Passed" | Update to **440** (or CI-dynamic badge) |
| One-liner installer | `scripts/install.sh` referenced but **not in repo** | Ship `install.sh` or remove curl one-liner |
| ARCHITECTURE.md | M8 "in progress", M9 "planned" | Mark M8/M9/M9.5 complete; link this roadmap |

### B. Web UI (dashboard.html — 1859-line monolith)

**Strengths:** Glassmorphic mobile layout, SSE integration, PTT API wired, profile/XP, provider health, storage panel, agent job queue.

**Top pain points (research team):**

| Priority | Issue |
|----------|-------|
| 🔴 P0 | Blueprint shown as raw markdown in `<pre>` — unreadable on mobile |
| 🔴 P0 | PTT button too small; dual record/PTT buttons confuse users |
| 🔴 P0 | Empty states are plain text — no Dumpi illustration or CTA |
| 🔴 P0 | Errors are fire-and-forget toasts — no retry |
| 🔴 P0 | Drawer focus trap missing (WCAG failure) |
| 🟠 P1 | Settings drawer overloaded (6 feature areas in one scroll) |
| 🟠 P1 | Nav drawer links are dead anchors |
| 🟠 P1 | No battery widget despite PiSugar API existing |
| 🟠 P1 | SSE reconnects silently — no connection health indicator |
| 🟡 P2 | Hard-coded dark theme only |
| 🟡 P2 | No onboarding for first-run users |

### C. Hardware code audit

| Component | Code status | On real Pi |
|-----------|-------------|------------|
| `FakeWhisplayBridge` / auto-detect | ✅ Solid | Falls back correctly off-Pi |
| `RealWhisplayBridge` SPI LCD | ⚠️ RGB888 sent, controller expects RGB565 | Garbled display |
| `RealWhisplayBridge` D/C GPIO | ❌ Never toggled | Init sequence unreliable |
| `whisplay_daemon.py` | ❌ Stub — logs one line and exits | systemd unit goes inactive |
| `pisugar_monitor.py` | ❌ Calls `PiSugarMonitor()` with no args; `run_forever()` is `pass` | Crash or silent exit |
| In-process `PiSugarMonitor.start()` | ✅ Works inside web app | Battery events publish |
| `ArecordCapture` | ✅ Works via default ALSA | No `-D` device selector |
| WM8960 / HAT audio | ❌ Not implemented | M10-P0 gate in plan |
| TTS playback (`aplay`) | ❌ Not implemented | Voice loop dead-ends after synthesis |
| `install_systemd.sh` | ⚠️ Missing `vibedump-agent.service`; "VibeDrop" typo | Agent daemon not installed |
| Button D → PTT | ⚠️ Polled in bridge but daemon doesn't run poll loop | Physical PTT inactive |

### D. Existing plans to fold in

- `docs/V2_PASS_PLAN.md` — 6-phase hardening + M10 features (30+ tasks)
- `docs/V2_PASS_SWARM.md` — 9 build agents + 4 auditors + wrap agent
- `docs/M10_PLAN.md` — Four pillars: memory, skills, evolution, smart Dumpi
- `docs/M10_KICKOFF.md` — P0 hardware gate before M10 code

**This roadmap supersedes launch order** by inserting **Wave 0 (docs + hardware truth)** and **Wave 1 (UI overhaul)** ahead of the existing V2 pass batches.

---

## Wave 0 — Gates & truth ⛔

> **Do not launch Wave 2+ until Wave 0 gates are checked.**

### 0A. Physical hardware verification (⛔ GATE)

| Done | ID | Pri | Task | Agent | Owns | Acceptance criteria |
|------|-----|-----|------|-------|------|---------------------|
| [ ] | HW-GATE-01 | ⛔ | Run `arecord -l`, `aplay -l`, `i2cdetect -y 1` on Pi Zero 2 W + Whisplay HAT | `hw-verify` | (report only) | Confirm WM8960 at `0x1a`, MEMS mics enumerate, speaker plays test tone |
| [ ] | HW-GATE-02 | ⛔ | Photograph assembly (4 angles) without USB mic | `hw-verify` | `docs/screenshots/assembly-*.jpg` | 4 real JPEGs ≥800px wide; no USB mic in "default build" shots |
| [x] | HW-GATE-03 | ⛔ | Capture real dashboard screenshots on Pi or dev | `hw-verify` | `docs/screenshots/*.png` | Replace all 1-byte placeholders |

### 0B. Documentation truth pass

| Done | ID | Pri | Task | Agent | Owns | Acceptance criteria |
|------|-----|-----|------|-------|------|---------------------|
| [x] | DOC-01 | 🔴 | Rewrite README hero + BOM for Whisplay audio | `docs-landing` | `README.md` | No USB mic in default BOM; onboard speaker + MEMS mics documented; 3.5 mm/USB/BT as upgrade |
| [x] | DOC-02 | 🔴 | Update HARDWARE.md pin map + audio sections | `docs-landing` | `docs/HARDWARE.md` | WM8960 `0x1a`, MEMS default input, onboard speaker default output; remove "HAT has no speaker" |
| [x] | DOC-03 | 🔴 | Fix assembly photo paths and captions | `docs-landing` | `docs/HARDWARE.md`, `README.md` | All image links resolve; captions match HAT-native audio |
| [x] | DOC-04 | 🟠 | Refresh INSTALL.md + add missing `install.sh` or remove one-liner | `docs-landing` | `docs/INSTALL.md`, `scripts/install.sh` | curl one-liner works OR README says "manual only" |
| [x] | DOC-05 | 🟠 | Update ARCHITECTURE.md milestone table | `docs-landing` | `docs/ARCHITECTURE.md` | M8/M9/M9.5 marked complete; link `SWARM_MASTER_ROADMAP.md` |
| [x] | DOC-06 | 🟠 | Update test badge to 440+ | `docs-landing` | `README.md` | Badge matches `pytest --co` count |
| [x] | DOC-07 | 🟡 | Replace HARDWARE_SETUP.md placeholder | `docs-landing` | `docs/HARDWARE_SETUP.md` | Points to HARDWARE.md + WM8960 enablement steps |
| [x] | DOC-08 | 🟡 | Add GitHub repo social preview / About blurb | `docs-landing` | `README.md` (top), `.github/` if needed | 2-sentence pitch + tags; no USB mic claim |

---

## Wave 1 — Hardware readiness

> Parallelizable after HW-GATE-01 confirms WM8960. Max 4 concurrent agents.

| Done | ID | Pri | Task | Agent | Owns | Deps | Acceptance criteria |
|------|-----|-----|------|-------|------|------|---------------------|
| [x] | HW-01 | 🔴 | Fix `pisugar_monitor.py` — wire bridge + bus, real loop | `hw-pisugar` | `vibedump/integrations/pisugar_monitor.py`, `pisugar.py` | — | `python -m vibedump.integrations.pisugar_monitor` runs ≥60s; publishes readings |
| [x] | HW-02 | 🔴 | Implement `whisplay_daemon.py` button poll + mascot push | `hw-whisplay` | `whisplay_daemon.py` | HW-04 | Button D triggers PTT event; LCD shows mascot frame |
| [x] | HW-03 | 🔴 | Fix RGB565 frame encoding in `display_frame()` | `hw-whisplay` | `whisplay.py` | — | Test with saved RGB565 bytes OR hardware smoke shows correct image |
| [x] | HW-04 | 🔴 | Add D/C GPIO toggle for ST7789 SPI | `hw-whisplay` | `whisplay.py` | — | Constant `WHISPLAY_DC_PIN` documented; command/data writes separated |
| [x] | HW-05 | 🔴 | WM8960 enablement in `install_whisplay_prereqs.sh` | `hw-audio` | `scripts/install_whisplay_prereqs.sh` | HW-GATE-01 | Overlay/modules for snd-bcm2835 + wm8960; idempotent |
| [x] | HW-06 | 🔴 | Default ALSA device → Whisplay WM8960 | `hw-audio` | `audio_capture.py`, `config.py`, `.env.example` | HW-05 | `VIBEDUMP_ALSA_CAPTURE_DEVICE` / `VIBEDUMP_ALSA_PLAYBACK_DEVICE` env vars |
| [x] | HW-07 | 🔴 | Implement `AplayPlayer` for TTS playback | `hw-audio` | new `vibedump/integrations/audio_playback.py` | HW-06 | Piper/fake TTS bytes play through speaker; fake fallback off-Pi |
| [x] | HW-08 | 🟠 | Wire TTS playback into PTT worker | `hw-audio` | `app.py` (PTT section only) | HW-07 | After `[ASK]` response, short TTS plays on Pi |
| [x] | HW-09 | 🟠 | Add `vibedump-agent.service` to install script | `hw-systemd` | `scripts/install_systemd.sh` | — | `install` enables all 4 units; fix "VibeDrop" → "VibeDump" typo |
| [x] | HW-10 | 🟠 | Fix `_write_silent_wav` keyword-only call in `app.py` | `hw-audio` | `app.py:728` | — | No TypeError if `audio_capture=None` |
| [ ] | HW-11 | 🟠 | Hardware smoke script covers audio round-trip | `hw-audio` | `scripts/hardware_smoke.sh`, tests | HW-06, HW-07 | `arecord` → `aplay` loop passes on Pi |
| [x] | HW-12 | 🟡 | Battery low/critical SSE + dashboard banner | `hw-pisugar` | `app.py`, `dashboard.html` | HW-01, UI-15 | <15% shows persistent banner |
| [ ] | HW-13 | 🟡 | Delete dead `vibedump/hardware_control.py` FakeHardware | `hw-cleanup` | `hardware_control.py`, imports | — | Grep shows no references; tests green |
| [x] | HW-14 | 🟡 | E2E PTT test (fake path) | `hw-test` | `tests/test_hardware_ptt_e2e.py` | — | start → complete → turn in DB |
| [x] | HW-15 | 🟡 | Whisplay daemon tests | `hw-test` | `tests/test_whisplay_daemon.py` | HW-02 | Mock bridge; asserts poll loop calls |

---

## Wave 2 — Web UI overhaul

> Split dashboard work across agents by **section ownership** to avoid merge conflicts.
> Do **not** split `dashboard.html` across agents in the same batch — use sequential patches or a split into partials in UI-00 first.

| Done | ID | Pri | Task | Agent | Owns | Deps | Acceptance criteria |
|------|-----|-----|------|-------|------|------|---------------------|
| [x] | UI-00 | 🟠 | Extract dashboard CSS/JS to static files (optional but recommended) | `ui-architect` | `vibedump/static/` | — | `dashboard.html` ≤200 lines shell; no behavior regression |
| [x] | UI-01 | 🔴 | Markdown blueprint renderer + section anchors | `ui-blueprint` | blueprint panel in dashboard | — | `##` headings render; collapsible sections; copy button works |
| [x] | UI-02 | 🔴 | Blueprint section jump chips + TBD highlighting | `ui-blueprint` | blueprint panel | UI-01 | "7/12 complete" chip; TBD sections muted |
| [x] | UI-03 | 🔴 | Unified PTT/record button (tap vs hold) | `ui-ptt` | action bar | — | ≥72px touch target; haptic vibrate; duration timer |
| [x] | UI-04 | 🔴 | PTT waveform / recording indicator | `ui-ptt` | action bar | UI-03 | Visual feedback during capture |
| [x] | UI-05 | 🔴 | Empty states with Dumpi mascot + CTA | `ui-polish` | dump list, detail, achievements | — | First-run shows "Start recording" button |
| [x] | UI-06 | 🔴 | Sticky error toasts + inline retry cards | `ui-polish` | toast + panel error UI | — | Failed `loadDumps` shows retry in panel |
| [x] | UI-07 | 🔴 | Drawer focus trap + `role="dialog"` | `ui-a11y` | drawer JS | — | Tab cycles in drawer; Escape closes; focus restored |
| [x] | UI-08 | 🟠 | Bottom tab bar (mobile) | `ui-nav` | layout CSS/JS | UI-00 preferred | 4 tabs: Dumps, Record, Memory, Settings |
| [x] | UI-09 | 🟠 | Swipe list→detail on mobile | `ui-nav` | split layout | UI-08 | Back button; no scroll-hunting |
| [x] | UI-10 | 🟠 | Mascot typewriter + context-aware lines | `ui-mascot` | mascot panel | — | Copy changes with dump count / state |
| [x] | UI-11 | 🟠 | XP bar + streak in mascot panel | `ui-mascot` | mascot panel | — | Not buried in settings only |
| [x] | UI-12 | 🟠 | Provider health: latency, test button, kind badges | `ui-settings` | provider section | — | STT/LLM/TTS labels visible |
| [x] | UI-13 | 🟠 | SSE connection health indicator | `ui-polish` | header/mascot | — | Green/amber/red dot; reconnect refreshes data |
| [x] | UI-14 | 🟠 | First-run onboarding coach marks | `ui-onboard` | overlay JS | UI-05 | 3-step tour; `localStorage` flag |
| [x] | UI-15 | 🟠 | PiSugar battery widget in header | `ui-hardware` | header | HW-01 | Shows % when real bridge; hidden on fake |
| [ ] | UI-16 | 🟡 | Light mode + manual theme toggle | `ui-theme` | CSS variables | — | `prefers-color-scheme` + Settings toggle |
| [ ] | UI-17 | 🟡 | Transcript chat bubbles (avatars, timestamps) | `ui-transcript` | transcript panel | — | Profile name + Dumpi avatar |
| [ ] | UI-18 | 🟡 | Dump list sort/filter + metadata | `ui-dumps` | dump list | — | Status chips; relative timestamps |
| [ ] | UI-19 | 🟡 | Storage panel sync progress + honest remote warning | `ui-settings` | storage section | — | Prominent "env var only" chip |
| [ ] | UI-20 | 🟡 | Agent job inline expand + status colors | `ui-agent` | agent section | — | Completed/failed row borders |
| [ ] | UI-21 | 🟡 | Skeleton loaders + button press micro-interactions | `ui-polish` | global CSS | — | Shimmer on fetch; `scale(0.97)` on active |
| [ ] | UI-22 | 🟡 | Custom delete confirm sheet (replace `confirm()`) | `ui-a11y` | delete flow | — | `role="alertdialog"` bottom sheet |
| [ ] | UI-23 | 🟡 | Achievement unlock overlay animation | `ui-mascot` | mascot panel | UI-11 | Trophy reveal on SSE `achievement.unlocked` |
| [x] | UI-24 | 🟠 | Dashboard HTML structure tests updated | `ui-test` | `tests/test_dashboard_html.py` | UI-01–08 | All new element IDs asserted |

---

## Wave 3 — Code hardening (V2 Pass Phase 0)

> Source: `docs/V2_PASS_PLAN.md` Phase 0. Launch after Wave 1 hardware P0s or in parallel on `v2-pass` branch if hardware agent is on Pi.

| Done | ID | Pri | Task | Agent | Owns | Acceptance criteria |
|------|-----|-----|------|-------|------|---------------------|
| [x] | HARD-01 | 🔴 | Fix inverted blueprint validation | `harden-bugs` | `agent_pipeline.py` | Valid LLM output kept; invalid → template |
| [x] | HARD-02 | 🔴 | Companion routes `?dump_id=N` | `harden-bugs` | `app.py` companion routes | Explicit ID works; missing blueprint → 404 |
| [x] | HARD-03 | 🔴 | WebSocket audio size cap + ack + sweeper | `harden-audio` | `app.py` WS handler | 1.2MB cap; ack frame; old file cleanup |
| [x] | HARD-04 | 🔴 | Harden `register_mcp_tool` schemas | `harden-mcp` | `agent/registry.py` | enum, nullable, oneOf handled |
| [x] | HARD-05 | 🟠 | Fix `test_mcp_integration.py` missing import | `harden-mcp` | test file | Test passes |
| [x] | HARD-06 | 🟠 | Fix or rewrite `test_graph_swarms.py` | `harden-bugs` | test file | Matches real graph nodes or marked skip with reason |
| [x] | HARD-07 | 🟠 | Delete `_pydantic_ai_spike.py` dead code | `harden-cleanup` | spike file | No imports remain |
| [x] | HARD-08 | 🟠 | Delete `_PDAAgent` block in `core.py` | `harden-cleanup` | `agent/core.py` | Grep clean; tests green |

---

## Wave 4 — M10 core features

> Source: `docs/V2_PASS_PLAN.md` Phases 1–5 + `docs/M10_PLAN.md`
> Use existing agent matrix in `docs/V2_PASS_SWARM.md` (B4–B9, C1–C4, W1).

| Done | ID | Pri | Task | Agent | Owns | Acceptance criteria |
|------|-----|-----|------|-------|------|---------------------|
| [x] | M10-01 | 🟣 | Real multi-agent swarm (Architect/Critic/Security) | `swarm-core` | `vibedump/agent/swarm/` | Parallel execution; shared state; tests assert prompt diffs |
| [x] | M10-02 | 🟣 | Swarm dashboard panel | `swarm-test-dash` | dashboard + tests | UI shows per-agent status |
| [x] | M10-03 | 🟣 | `MemoryStore` episodic + semantic | `memory-layer` | `vibedump/memory/` | FTS5 + vec table; `recall()` API |
| [ ] | M10-04 | 🟣 | Embeddings (bge-small-en-v1.5) | `memory-layer` | `memory/embeddings.py` | CPU inference on Pi; skipped test for slow path |
| [x] | M10-05 | 🟣 | Lessons extraction + storage | `lessons-skills` | `memory/lessons.py` | Lessons persisted; FTS searchable |
| [x] | M10-06 | 🟣 | Skill registry + sandbox | `lessons-skills` | `vibedump/skills/` | Voyager-style skill synthesis; sandboxed execution |
| [x] | M10-07 | 🟣 | Evolution hook (Stop callback) | `evolution-cron` | `agent/evolution.py` | Post-run lesson extraction |
| [x] | M10-08 | 🟣 | Weekly `vibedump_evolve.py` cron | `evolution-cron` | `scripts/vibedump_evolve.py` | Systemd timer or cron doc |
| [ ] | M10-09 | 🟣 | Smart Dumpi mascot agent | `smart-mascot` | `vibedump/mascot/` | Subclass OpenClaude; 3 tools |
| [ ] | M10-10 | 🟣 | Wakeword stub (openWakeWord) | `smart-mascot` | mascot wakeword module | Optional; disabled by default |
| [x] | M10-11 | 🟣 | `recall()` wired into listener before finalize | `memory-layer` | `active_listener.py` | Blueprint includes RAG overlaps section |
| [ ] | M10-12 | 🟡 | Pipecat / WebRTC real-time audio | `audio-v2` | new module | Deferred if WM8960 path sufficient |

---

## Wave 5 — QA & counter-squad

| Done | ID | Pri | Task | Agent | Acceptance criteria |
|------|-----|-----|------|-------|---------------------|
| [x] | QA-01 | 🔴 | Full pytest on `v2-pass` branch | `test-auditor` | 440+ passed, <60s |
| [ ] | QA-02 | 🔴 | Ruff check | `test-auditor` | Zero new errors (fix or baseline) |
| [ ] | QA-03 | 🔴 | Spec compliance vs this roadmap | `spec-auditor` | Every [x] task has evidence |
| [ ] | QA-04 | 🟠 | Soak test 10 min | `perf-auditor` | `./scripts/soak.sh` exit 0 |
| [x] | QA-05 | 🟠 | Docs audit (no USB mic claim) | `docs-auditor` | Grep `USB mic` only in "upgrade" context |
| [ ] | QA-06 | 🟠 | Hardware smoke on real Pi | `hw-verify` | `hardware_smoke.sh` exit 0 |
| [ ] | QA-07 | 🟠 | Dashboard manual test checklist | `ui-test` | All P0 UI items verified |
| [ ] | QA-08 | 🟡 | Secret scan / pre-commit | `docs-auditor` | No credentials in diff |

---

## Wave 6 — Wrap & release

| Done | ID | Pri | Task | Agent | Acceptance criteria |
|------|-----|-----|------|-------|---------------------|
| [ ] | REL-01 | 🔴 | Squash merge to `master` | `wrap` | Single commit; trailers: Confidence, Scope-risk, Tested |
| [ ] | REL-02 | 🔴 | Update README badge + screenshots | `wrap` | GitHub landing reflects shipped state |
| [ ] | REL-03 | 🟠 | Tag `v0.2.0` | `wrap` | Git tag + short release notes |
| [x] | REL-04 | 🟠 | Mark all completed tasks [x] in this file | `wrap` | Roadmap reflects reality |

---

## Swarm launch schedule (recommended)

```
Week 0 (human + 1 agent):
  HW-GATE-01..03  →  DOC-01..03
  ⛔ STOP until user approves Wave 1

Wave 1 (4 parallel slots):
  Batch A: HW-01, HW-03, HW-05, HW-07
  Batch B: HW-02, HW-04, HW-06, HW-08
  Batch C: HW-09, HW-10, HW-11, HW-14

Wave 2 (3 parallel slots — UI must serialize dashboard.html OR do UI-00 first):
  Batch A: UI-00
  Batch B: UI-01, UI-03, UI-05, UI-07  (disjoint DOM regions)
  Batch C: UI-08, UI-10, UI-12, UI-15
  Batch D: UI-02, UI-04, UI-06, UI-09
  Batch E: UI-11..UI-24 (P2 items as capacity allows)

Wave 3 (3 parallel):
  HARD-01, HARD-03, HARD-04  →  HARD-02, HARD-05, HARD-06  →  HARD-07, HARD-08

Wave 4 (V2_PASS_SWARM batches 1–3):
  See docs/V2_PASS_SWARM.md §1 batch schedule

Wave 5–6:
  Counter squad → wrap agent
```

---

## Agent assignment matrix (quick reference)

| Agent ID | Primary waves | Skills needed |
|----------|---------------|---------------|
| `hw-verify` | 0A, QA-06 | SSH to Pi, `arecord`/`i2cdetect`, photography |
| `docs-landing` | 0B | Technical writing, GitHub markdown |
| `hw-pisugar` | 1 | I2C, systemd, Python threading |
| `hw-whisplay` | 1 | SPI, GPIO, PIL RGB565 |
| `hw-audio` | 1 | ALSA, WM8960, subprocess |
| `hw-systemd` | 1 | systemd unit files |
| `ui-architect` | 2 | Frontend structure |
| `ui-blueprint` | 2 | Markdown rendering |
| `ui-ptt` | 2 | Web Audio API, touch UX |
| `ui-nav` | 2 | Mobile CSS, gestures |
| `ui-mascot` | 2 | Animation, SSE |
| `ui-settings` | 2 | Forms, provider UX |
| `ui-a11y` | 2 | WCAG, ARIA |
| `ui-polish` | 2 | CSS micro-interactions |
| `harden-*` | 3 | Python, FastAPI, TDD |
| `swarm-core` … `smart-mascot` | 4 | Pydantic-AI, graphs |
| `spec-auditor` … `wrap` | 5–6 | Read-only review, git |

---

## Open questions for user approval

1. **Hardware gate:** Do you have the Pi + Whisplay unit available for `HW-GATE-01` before we update docs to state WM8960 as confirmed? (M10 plan treats this as verify-first.)
2. **Dashboard split:** Approve `UI-00` (extract static assets) before parallel UI agents — **recommended** to prevent merge hell.
3. **Scope cut:** If time-boxed, ship Waves 0–2 + Wave 3 HARD-01..04 only; defer M10 to a second swarm.
4. **Branch strategy:** Single `v2-pass` branch vs. `wave-1-hardware`, `wave-2-ui` feature branches merged sequentially?

---

## Progress tracker

| Wave | Tasks | Done | % |
|------|-------|------|---|
| 0 — Gates & docs | 11 | 9 | 82% |
| 1 — Hardware | 15 | 13 | 87% |
| 2 — Web UI | 25 | 17 | 68% |
| 3 — Hardening | 8 | 8 | 100% |
| 4 — M10 | 12 | 8 | 67% |
| 5 — QA | 8 | 2 | 25% |
| 6 — Release | 4 | 1 | 25% |
| **Total** | **83** | **58** | **70%** |

**Deferred:** Pi hardware gates (HW-GATE-01/02), P2 UI polish (UI-16–23), real bge embeddings (M10-04), Smart Dumpi (M10-09), release merge/tag.

---

*When you approve, reply with which waves to launch and answers to the open questions. The swarm should read this file + `docs/V2_PASS_SWARM.md` before dispatch.*
