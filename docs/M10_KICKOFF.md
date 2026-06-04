# M10 Kickoff — Session Handover

> **Read this first.** Pickup note for a fresh chat that is about
> to start M10 work. M9.5 is shipped and the full Pi Zero 2 W
> retarget is committed. M10 is the *self-learning* milestone.

## TL;DR

We just turned Vibe-Dump from a voice-to-blueprint pipeline into a
**self-learning vibe-coding machine**. The plan lives at
[`docs/M10_PLAN.md`](M10_PLAN.md). Five phases: P0 (hardware
verify) → P1 (RAG memory) → P2 (skill synthesis) → P3 (evolution
hooks) → P4 (smart Dumpi). Read the plan, then start with P0.

## What shipped this session (prior)

1. **M9.5 — openlaude runtime upgrade** (`a06d5f2`).
   pydantic-ai + pydantic-graph + instructor under a new `[agent]`
   extra in `pyproject.toml`. Public API unchanged.
2. **Test audit** — 430 pass in ~15-30s. Pyright info-only. Secret
   hook clean.
3. **README polish + push** (`bad90b6`, `dc9f077`) — professional
   landing page on `NaustudentX18/vibe-dump`.
4. **Pi Zero 2 W retarget sweep** — two commits:
   - `ee67e34 docs: retarget hardware from Pi 5 to Pi Zero 2 W`
     (docs + BOM prose)
   - `20d1b6f fix(hw): full Pi Zero 2 W retarget sweep (whisper,
     BOM, systemd)` — `whisper_stt.default_model_size` flipped to
     `tiny.en` (512 MB Zero can't fit `base.en`), README BOM
     corrected for "no audio jack on the Zero 2 W", all 4 systemd
     units repointed to `NaustudentX18/vibe-dump`

## Repo state

```
master: 20d1b6f fix(hw): full Pi Zero 2 W retarget sweep        ← HEAD
        ee67e34 docs: retarget hardware from Pi 5 to Pi Zero 2 W
        ce44c3f docs(handover): session pickup notes (M9.5 shipped, M10 next)
        dc9f077 docs(readme): restore voice-capture phrase + placeholder marker
        bad90b6 docs(readme): professional landing page with badges, screenshots, M9.5 notes
        a06d5f2 feat(agent): M9.5 — openlaude runtime on pydantic-ai + pydantic-graph + instructor
remote: origin -> https://github.com/NaustudentX18/vibe-dump.git
```

Working tree clean. Untracked `vibe_dump.egg-info/` is harmless
build output.

## Test landscape

- 430 tests, all pass in ~15-30s (cold vs warm pytest).
- `tests/test_docs.py` pins README structure — `Voice capture`,
  `Whisplay`, `PiSugar`, `rclone`, `Whisper`, `Piper`, `LLM`,
  `SQLite`, `mascot`, `XP` must all appear in the features list.
  `<placeholder>` must remain in the curl one-liner.
- `tests/test_audio_adapters.py` has no model-size pin; safe to
  change defaults.
- `tests/test_whisplay_bridge.py` was updated to say "0-27 on a
  Pi Zero 2 W" instead of the old Pi 5 wording.

## Key files for M10 pickup

- **`docs/M10_PLAN.md`** — the actual plan. Read this end-to-end
  before writing any code. It is the source of truth for the four
  pillars, the file layout, and the phase acceptance criteria.
- `vibedump/agent/core.py` — `OpenClaude`, `PydanticAIBackend`,
  `_publish_trace`. The agent's runtime. Pillar 3's Stop hook
  plugs in here.
- `vibedump/agent/tools.py` — `ToolDefinition(Generic[InputT])`
  and the 9 Pydantic input models. New memory / skill tools slot
  into this file's `TOOL_REGISTRY`.
- `vibedump/agent_pipeline.py` — the listener loop. Pillar 1's
  `recall()` call goes in `finalize_blueprint`, before the prompt
  is sent to the LLM.
- `vibedump/active_listener.py` — frozen Pydantic `ListenerDecision`
  / `Blueprint`. No changes expected here; memory is upstream.
- `vibedump/database.py` — already has `update_dump_metadata()`.
  Pillar 1's `MemoryStore` is a *new* sqlite file
  (`data/memory.sqlite3`), not a column on `dumps`.
- `vibedump/app.py:_wire_agent_runtime` — passes `bus=state.bus`
  to `OpenClaude`. The new `agent.evolution` SSE event rides the
  same bus.
- `vibedump/static/dashboard.html` — Trace panel already exists
  (M9.5). Pillar 4 adds a "Ask Dumpi" textbox to it; the rest of
  the dashboard is unchanged.
- `vibedump/integrations/whisplay.py` — pin map constants are the
  source of truth. Pillar 0 (hardware verify) may add a `MIC_PINS`
  block if the WM8960 is confirmed.
- `scripts/install_whisplay_prereqs.sh` — Pillar 0 extends this
  with the WM8960 overlay and I2C module load.
- `vibedump/providers/whisper_stt.py` — `default_model_size =
  "tiny.en"` (M9.5 retarget). Wakeword model in Pillar 4 must be
  smaller than this — openWakeWord tiny (~30 MB) is the default.

## Pillar 0 — the gate (do this first)

The Whisplay HAT we ship against is, per the current Waveshare /
PiSugar revision (Amazon ASIN B0FPG8S6K6), a **WM8960 stereo
codec + dual MEMS mics + onboard speaker** — not just an LCD. Our
`HARDWARE.md` and install script only document the LCD path.

**Verify on hardware before writing any M10 code.** From the Pi
Zero 2 W:

```bash
arecord -l                       # should show bcm2835-codec-decode or wm8960
arecord -D plughw:0,0 -d 3 test.wav  # capture 3 s, listen back
aplay -l                         # onboard speaker + line-out
sudo i2cdetect -y 1              # WM8960 at 0x1a
```

If the WM8960 is present:

- Add `WM8960 codec (I2C 0x1a) + dual MEMS mics + onboard speaker`
  to `docs/HARDWARE.md`
- Drop "USB microphone" from the README BOM; replace with "Whisplay
  HAT dual MEMS microphones" with a footnote
- Extend `scripts/install_whisplay_prereqs.sh` to load
  `snd-bcm2835` and `wm8960` codec
- The BOM still lists USB / Bluetooth speaker for fuller audio;
  the onboard speaker is the default for short follow-ups

If the WM8960 is **not** present, the retarget from M9.5 stays
correct and M10 still ships — Pillar 4's wakeword falls back to
button-only and mascot queries come through the dashboard.

This is the only task that requires the physical Pi on the bench.
Everything else can be developed and tested on the dev host.

## Constraints to preserve (verbatim from M9.5)

- **Naming policy.** Identifiers MUST NOT contain
  `api_key`, `secret`, `token`, or `password`. The pre-commit
  secret hook greps staged diffs for
  `(api[_-]?key|secret|token|password)[[:space:]]*[:=]`. Bare
  `{"api_key": "..."}` literals in test data are fine because the
  `"` breaks the regex. Use `kind`, `name`, `directive`, `prefix`,
  `value`, `field` in Python identifiers.
- **Pyright info-only** at the swarm stage. The pre-commit hook
  does not run pyright. Real type bugs get fixed in-flight; cosmetic
  warnings batch for the wrap.
- **Commit-msg lore trailers.** `Confidence: low|medium|high`,
  `Scope-risk: narrow|moderate|broad`, and one of
  `Tested: <evidence>` or `Not-tested: <reason>`. No attribution
  footer.
- **No commits during agent work.** Each phase finishes, reports,
  and the wrap agent does the single squash for the phase. Same
  pattern as M9.5's `a06d5f2`.
- **pydantic-graph v1.x pin.** `pydantic_graph.Graph` is deprecated
  in v2; we keep `pydantic_graph<2` in `pyproject.toml`. Migration
  is a separate M11 candidate.

## M10 operational notes

- **.venv** is the Python runtime. `pip install -e ".[all]"`
  pulls everything including pydantic-ai, pydantic-graph, instructor.
  Pillar 1 also wants `sqlite-vec` (or `vec0` via the system sqlite
  if Bookworm ships a recent enough version) and a sentence-transformers
  runtime for bge-small. Pillar 2 wants `watchfiles`. Pillar 4 wants
  `openwakeword`. Add them under the existing `[all]` extra, not as
  new extras.
- **PC Ollama endpoint.** `desktop-ujsii52.local:11434` is the
  heavy lifter. `qwen3-14b-agent` (~35 tok/s) is the default. For
  embeddings the PC has `nomic-embed-text` (137 M params, F16, 0.3
  GB) — that's the warm-path embedder. Cold path is bge-small on
  the Pi.
- **Hot reload matters.** Skills live in `~/.vibedump/skills/` and
  the registry uses `watchfiles`. Make sure `.gitignore` excludes
  that directory; the SD card is the persistence layer, not git.
- **The mascot is real now.** `data/dumpi_persona.json` is the
  editable knob. Ship a sane default ("terse + dry wit, ≤ 12 words
  on the HAT, ≤ 60 on the dashboard, never use the word
  `utilize`"). Don't ship a default that *also* needs editing
  before it works.
- **Pillar 3's vibe_score.json is the demo artifact.** The user
  wants to *see* Dumpi get smarter. The weekly cron's output
  (success rate, top-5 skills, freshest lessons) is what the
  dashboard's Storage panel surfaces. Make it pretty.

## Phase acceptance (short form)

| Phase | Gate |
|-------|------|
| **P0** | `arecord -l` shows the WM8960; HARDWARE.md + BOM + install script updated; 430 tests still green |
| **P1** | `MemoryStore.recall` returns relevant hits; `learn` round-trips; 430 + ≥ 10 new tests pass |
| **P2** | A demo skill is synthesized, sandbox-tested, hot-loaded; bad skill is rejected; 430 + ≥ 8 new tests pass |
| **P3** | Stop hook fires `agent.evolution`; `vibedump_evolve.py` produces a valid `vibe_score.json`; 430 + ≥ 5 new tests pass |
| **P4** | Wakeword routes to mascot; "Hey Dumpi, what did I dump last Tuesday?" answers from memory; mascot visible on HAT; 430 + ≥ 7 new tests pass |

Per-phase squash commit with lore trailers, per the M9.5
convention. No giant single M10 commit at the end.

## Verification recipe

```bash
cd /home/pi/vibe-dump
.venv/bin/python -m pytest -q                # baseline 430
git log --oneline -3                          # HEAD on 20d1b6f
git remote -v                                 # origin -> NaustudentX18/vibe-dump
ls docs/M10_PLAN.md docs/M10_KICKOFF.md       # both present
```

The system is in a known-good state; the agent is ready to
*remember*, *synthesize*, *learn*, and *speak*. P0 first.
