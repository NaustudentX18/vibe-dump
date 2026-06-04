# M10 Plan — Self-Learning Vibe-Coding Machine

> **Thesis.** Vibe-Dump is no longer a voice-to-blueprint pipeline. From
> M10 forward the agent *remembers* every dump, *synthesizes* new tools
> when it spots a gap, *learns* from mistakes, and exposes all of that
> through Dumpi — a live mascot with a real brain, not a sprite.

The 512 MB Pi Zero 2 W stays the canonical host. The PC Ollama
endpoint (`desktop-ujsii52.local:11434`) is still the heavy lifter
for the LLM; the Pi owns the loop, the state, the persistence, and
the personality.

## Hardware reality check (verify at M10 start)

The Whisplay HAT we ship against is **not** just an LCD + LED +
buttons. The current Waveshare / PiSugar revision (Amazon ASIN
B0FPG8S6K6) integrates:

- **WM8960 stereo codec** on I2C
- **Dual MEMS microphones** (left/right channels)
- **Onboard speaker** (small, fine for the listener's follow-ups)
- **3.5 mm line-out jack** for an external speaker
- ST7789 240×280 LCD, 4 buttons, WS2812 LED (already documented)

If confirmed on the unit in hand, the BOM collapses:

- **Drop** standalone USB mic (Whisplay's MEMS pair covers it)
- **Keep** the 3.5 mm / USB / Bluetooth speaker row as the *upgrade*
  path for fuller audio; document the onboard speaker as the default
- **Add** the WM8960 I2C address (0x1a) to the pin map
- **Extend** `scripts/install_whisplay_prereqs.sh` to enable the
  `snd-bcm2835` overlay and load `wm8960` if not auto-bound

This is **task M10-P0** — the gate. Nothing else in M10 ships until
the audio surface is verified, because P4 (Smart Dumpi) and the
memory layer's `audio_path` field both assume it.

## Architecture delta from M9.5

```
M9.5                                     M10
─────                                    ────
vibedump/agent/  ── pydantic-ai agent    vibedump/agent/        (unchanged surface)
vibedump/active_listener.py              vibedump/active_listener.py  (+ recall() before finalize)
vibedump/database.py (dumps.sqlite3)     vibedump/database.py        (unchanged)
                                         vibedump/memory/store.py   ★ NEW
                                         vibedump/memory/embeddings.py ★ NEW
                                         vibedump/memory/lessons.py ★ NEW
                                         vibedump/skills/registry.py ★ NEW
                                         vibedump/skills/sandbox.py ★ NEW
                                         vibedump/agent/evolution.py ★ NEW
                                         vibedump/mascot/agent.py   ★ NEW (extends OpenClaude)
data/dumps.sqlite3                       data/dumps.sqlite3          (unchanged)
                                         data/memory.sqlite3         ★ NEW (FTS5 + vectors)
scripts/install_whisplay_prereqs.sh      scripts/install_whisplay_prereqs.sh  (+ WM8960)
                                         scripts/vibedump_evolve.py  ★ NEW (weekly cron)
                                         ~/.vibedump/skills/         ★ NEW (synthesized tools, gitignored)
```

Public surface (HTTP API, dashboard, Whisplay buttons) is unchanged.
Everything new sits behind the existing `agent.trace` SSE event.

## The four pillars

### Pillar 1 — Persistent memory (RAG on the SD card)

Two stores, one orchestrator.

- **Episodic store.** `dumps.fts` — SQLite FTS5 over every
  `transcript`, every `blueprint.summary`, every
  `decisions.text`. Cheap, exact, and good enough for keyword
  recall ("that one dump about the rclone config").
- **Semantic store.** `dumps.vec` — sqlite-vec or the `vec0`
  virtual table, holding embeddings for the same fields. Embeddings
  come from **bge-small-en-v1.5** (int8, ~33 M params, ~130 MB
  RAM) running **on the Pi** for cold paths, or **bge-m3** via the
  PC Ollama endpoint for warm paths. We default to Pi-local and
  offload only on miss + low-confidence.
- **Lesson store.** `lessons.fts` — extracted from completed
  dumps. Two flavors:
  - *negative*: "listener said X, user said Y, diff is Z, don't do
    X again" — auto-derives a guard clause for the next prompt
    template
  - *positive*: "user pattern P led to blueprint B, reuse P next
    time" — promotes to a retrieval heuristic
- **API.** `MemoryStore.recall(query, k=5, *, modality="text"|"audio")`
  returns a `RecallHit` list. `learn(fact, kind, evidence)` appends
  to the right store. `forget(stale)` prunes.

A **Stop hook** on the agent (Pillar 3) calls `learn` after every
dump. The active listener calls `recall` before finalizing a
blueprint so the prompt carries "here's what worked last time we
heard something like this."

### Pillar 2 — Skill synthesis (the Voyager move)

Voyager (Wang et al., 2023, arXiv:2305.16291) demonstrated that an
LLM agent can build a persistent **skill library** of executable
code blocks, each with a natural-language description, and reuse
them across tasks. We adopt the same shape, scoped to Vibe-Dump's
world.

- **Skill = a single Python function + a JSON manifest.** Manifest
  carries: `name`, `description`, `input_schema` (Pydantic model),
  `examples` (a few `(input, output)` pairs), `success_count`,
  `failure_count`, `last_used`, `created_from_dump_id`.
- **Sandbox.** `vibedump/skills/sandbox.py` runs a candidate skill
  in a subprocess with a timeout, a `syscall_filter` (rlimits on
  open/write/network), and a deterministic test fixture. If it
  passes 3+ examples it graduates from `_staging/` to the live
  catalog.
- **Storage.** Live skills live in `~/.vibedump/skills/<name>.py`.
  Gitignored, SD-card persistent, and *survive* a re-image if the
  user runs `vibedump-backup.sh` (added in P3).
- **Registry hot-reload.** `vibedump/skills/registry.py` watches
  the directory with `watchfiles` and re-registers on change. No
  service restart needed; the dashboard's Trace panel (already
  there from M9.5) surfaces the newly registered tool name on
  next `agent.trace` event.
- **What gets synthesized.** Examples we'd expect to see emerge:
  - "extract action items from a freeform transcript" (reusable
    after the 5th time the listener gets it wrong)
  - "render a custom mascot pose for a given XP level" (reuses
    the rasterizer with a new expression)
  - "diff two blueprints and emit a changelog" (after the 2nd
    bundle re-import)

### Pillar 3 — Self-evolution hooks (Stop + weekly cron)

The agent has no idea it made a mistake. M10 fixes that by hooking
**two** lifecycle points:

- **Stop hook** (`vibedump/agent/evolution.py`). On every agent
  run that ends (success or failure), a tiny post-run pass:
  1. Diff the agent's intended action against the user-confirmed
     outcome (e.g. blueprint accepted vs edited vs deleted)
  2. If accepted unchanged → extract the *positive* pattern into
     `lessons.fts` and bump the matching skill's `success_count`
  3. If edited → diff to find the smallest edit, log as
     *corrective feedback*, and update the relevant skill's
     manifest `examples` with the corrected input/output
  4. If deleted → flag the underlying pattern as a *negative*,
     search the prompt templates for the offending heuristic, and
     emit a guard-clause PR-style patch into `.omc/patches/`
  5. Emit `agent.evolution` SSE event so the dashboard's
     existing Trace panel can show "Dumpi learned: <one-liner>"

- **Weekly cron** (`scripts/vibedump_evolve.py`). Runs Sunday 03:17
  local (off-peak, easy to remember). Jobs:
  1. Consolidate near-duplicate lessons (cosine > 0.92 → merge)
  2. Prune lessons that haven't fired in 90 days
  3. Re-embed the full corpus with a fresh bge model if a newer
     one is available locally
  4. Emit a `vibe_score.json`: success_rate, mean_recall_precision,
     top-5 most-reused skills, top-3 freshest lessons
  5. Optionally post a digest to the dashboard's Storage panel

This is the difference between "tool that runs" and "tool that gets
*better* every week without a human in the loop."

### Pillar 4 — Smart Dumpi

The mascot was always a smiley face on a 240×280 panel. From M10 on
it's a live agent.

- **State.** `vibedump/mascot/state.py` (frozen Pydantic):
  `mood` (idle | curious | working | proud), `xp_level`, `last_lesson`,
  `recent_recall` (last 5 `RecallHit` summaries).
- **Agent.** `vibedump/mascot/agent.py` is an `OpenClaude` subclass
  with three tools: `mascot.recall(query)`, `mascot.dumpi_says(...)`,
  `mascot.xp_award(reason)`. It runs on the same pydantic-ai
  backend as the listener.
- **Trigger surfaces.**
  - **Voice.** "Hey Dumpi" wakeword (openWakeWord tiny model on
    the Whisplay's WM8960 mics, runs in <50 MB RAM) routes the
    next utterance to the mascot agent instead of the listener
  - **Button.** Long-press Button A = "Dumpi, what do you know
    about X?" mapped to `mascot.recall` and a one-liner on the
    speaker
  - **Dashboard.** A "Ask Dumpi" textbox in the Trace panel that
    sends the query through the same agent
- **Display.** The Whisplay HAT LCD compositor (already in
  `mascot_renderer.py`) gains a third layer: mood pose + XP ring
  + most-recent lesson ticker. Driven by the mascot state, not
  the dump state. Dumpi is always *someone*, not just a state
  indicator.
- **Personality.** Constrained by a `DumpiPersona` Pydantic model
  loaded from `data/dumpi_persona.json`: tone ("terse + dry wit"
  by default), banned phrases, response budget (≤ 12 words on
  the HAT, ≤ 60 words on the dashboard).

## Phased delivery

Each phase is shippable, all 430 existing tests must keep passing,
and the commit-msg lore trailers stay mandatory.

| Phase | Scope | Acceptance |
|-------|-------|-----------|
| **P0** | Verify Whisplay audio surface on hardware; update HARDWARE.md, BOM, install script | `arecord -l` shows the WM8960; dual mics enumerated; `aplay` works on the onboard speaker or line-out |
| **P1** | Memory layer (Pillar 1) | `MemoryStore.recall` returns relevant hits for a seeded corpus; `learn` round-trips; existing 430 tests still green; new tests for FTS5 + vec recall (≥ 95% on a 50-dump fixture) |
| **P2** | Skill synthesis (Pillar 2) | A demo skill "extract_action_items" is synthesized from 3 successful dumps, sandbox-tested, and hot-loaded into the registry; a bad skill (open/network) is rejected by the sandbox |
| **P3** | Evolution hooks (Pillar 3) | Stop hook emits `agent.evolution` events; `vibedump_evolve.py` cron produces a valid `vibe_score.json` on a 100-dump fixture; weekly cron is registered in install script |
| **P4** | Smart Dumpi (Pillar 4) | Wakeword on the Whisplay mics routes to the mascot; "Hey Dumpi, what did I dump last Tuesday?" produces a real answer from the memory layer; mascot pose + mood + lesson ticker visible on the HAT |

Estimated wall-clock per phase: P0 = 0.5 day (just hardware time),
P1 = 2 days, P2 = 3 days, P3 = 2 days, P4 = 3 days. Total ≈ 10
working days end-to-end if the agent swarm handles the build and
the human handles the hardware step.

## File-level plan (new code only)

```
vibedump/memory/
  __init__.py
  store.py          # MemoryStore: FTS5 + vec, recall/learn/forget
  embeddings.py     # Embedder: tries bge-small on Pi, falls back to PC Ollama
  lessons.py        # LessonExtractor: parses transcripts into +/- lessons
  schema.py         # RecallHit, Lesson, SkillManifest Pydantic models
  backends/
    sqlite_fts.py   # FTS5 backend
    sqlite_vec.py   # vec0 backend
vibedump/skills/
  __init__.py
  registry.py       # hot-reload, exposes to OpenClaude
  sandbox.py        # subprocess + rlimits + syscall filter
  synthesizer.py    # LLM turns a successful pattern into a skill
  examples/
    extract_action_items.py    # demo skill
vibedump/agent/
  evolution.py      # Stop hook, agent.evolution SSE event
vibedump/mascot/
  agent.py          # OpenClaude subclass, the live Dumpi brain
  state.py          # frozen Pydantic state
  wakeword.py       # openWakeWord tiny on the WM8960 mics
scripts/
  vibedump_evolve.py
  vibedump-backup.sh    # tarball ~/.vibedump/skills + memory.sqlite3
tests/
  test_memory_store.py
  test_memory_recall.py
  test_skill_sandbox.py
  test_skill_synthesis.py
  test_evolution_hook.py
  test_mascot_agent.py
  test_wakeword.py
data/
  dumpi_persona.json    # editable persona
docs/
  M10_PLAN.md           # this file
  M10_KICKOFF.md        # pickup handover
```

## Test plan

| Test | What it proves | Where |
|------|----------------|-------|
| `test_memory_recall_relevant_first` | FTS5 + vec recall orders hits by relevance on a 50-dump fixture | `test_memory_recall.py` |
| `test_memory_learn_round_trip` | Insert → query → assert exact hit | `test_memory_store.py` |
| `test_skill_sandbox_rejects_network` | A skill that imports `socket` is refused at sandbox time | `test_skill_sandbox.py` |
| `test_skill_sandbox_rejects_filesystem_escape` | A skill that writes outside `$TMPDIR` is refused | `test_skill_sandbox.py` |
| `test_skill_synthesis_promotes_after_three_passes` | Synthesizer graduates a candidate after 3/3 example passes | `test_skill_synthesis.py` |
| `test_evolution_emits_event_on_dump_complete` | Stop hook fires `agent.evolution` with a real lesson summary | `test_evolution_hook.py` |
| `test_vibedump_evolve_consolidates_near_dupes` | Two lessons with cosine 0.95 merge into one | `tests/integration/test_evolve_cli.py` |
| `test_mascot_recall_uses_memory_store` | "Hey Dumpi, last Tuesday's dump" hits the memory layer | `test_mascot_agent.py` |
| `test_mascot_persona_respects_word_budget` | HAT responses ≤ 12 words; dashboard ≤ 60 | `test_mascot_agent.py` |
| `test_wakeword_routes_to_mascot_agent` | "Hey Dumpi" utterance → mascot agent, not listener | `test_wakeword.py` |

Coverage target: 80% (per project rules). The 430-test baseline
must stay green through every phase.

## Constraints carried from M9.5

Verbatim — these do not get re-litigated.

- **Naming policy.** No `api_key`, `secret`, `token`, `password`
  substrings in identifiers. Pre-commit hook greps staged diffs
  for `(api[_-]?key|secret|token|password)[[:space:]]*[:=]`. Use
  `kind`, `name`, `directive`, `prefix`, `value`, `field`. Bare
  `{"api_key": "..."}` literals in test data are fine because the
  `"` breaks the regex.
- **Pyright info-only** at the swarm stage. The pre-commit hook
  does not run pyright. Real type bugs get fixed in-flight; cosmetic
  warnings batch for the wrap.
- **Commit-msg lore trailers.** `Confidence: low|medium|high`,
  `Scope-risk: narrow|moderate|broad`, and `Tested: <evidence>` or
  `Not-tested: <reason>`. No attribution footer.
- **No commits during agent work.** Wrap agent does the single
  squash per phase. Same pattern as M9.5's `a06d5f2`.
- **pydantic-graph v1.x pin.** `pydantic_graph.Graph` is deprecated
  in v2; we keep `pydantic_graph<2` in `pyproject.toml` and revisit
  in M11.

## Open questions for M10-P0

- Is the Whisplay in hand the WM8960 revision, or an older one
  without the codec? (Amazon listing is for the new revision; the
  older SKU was LCD + LED + buttons only.)
- Does the user want openWakeWord (Pi-side, ~50 MB RAM) or a
  Whisper-based spotter (more accurate, ~150 MB)? Default: openWakeWord.
- Persona config: ship a default "terse + dry wit" or expose a
  `data/dumpi_persona.json` editor in the dashboard from day one?
  Default: editable, dashboard editor is a stretch goal.
- Should synthesized skills be world-readable or `chmod 600`?
  Default: 600 (they may contain user-context fragments).

## Verification recipe (per-phase)

```bash
cd /home/pi/vibe-dump
.venv/bin/python -m pytest -q                # baseline 430 + new tests
.venv/bin/python -m pytest -q tests/test_memory_recall.py
.venv/bin/python -m pytest -q tests/test_skill_sandbox.py
.venv/bin/python -m pytest -q tests/test_mascot_agent.py
git log --oneline -3                          # confirm squash + lore trailers
git remote -v                                 # origin -> NaustudentX18/vibe-dump
sqlite3 data/memory.sqlite3 ".schema"         # FTS5 + vec0 tables present
ls -la ~/.vibedump/skills/                    # synthesized skills live here
```

The point isn't to ship all five pillars at once. It's to ship
each one as a self-contained, tested, recoverable commit and let
Dumpi get a little smarter every phase.
