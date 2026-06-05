# Vibe-Dump V2 Pass — Agent Swarm Launch Block

> **Purpose:** Single source of truth for the parallel swarm that builds
> `V2_PASS_PLAN.md`. 7 build agents in 4 batches, then 1 wrap agent.
> Counter squad (4 agents) runs after batch 4 in parallel, then 1 final wrap.
>
> **Rule of the road:** Build agents do not commit. Counter agents do not
> commit. Only the wrap agent commits. The wrap agent also does the
> final `git add -A && git commit` with the 3 lore trailers.

---

## 1. Swarm matrix

| # | Agent | Type | Phase | Owns (exclusive) | Parallel with | Model | Budget |
|---|-------|------|-------|------------------|---------------|-------|--------|
| B1 | harden-bugs | executor | 0 | 0.1, 0.2 | none | sonnet | 1 file + 2 tests |
| B2 | harden-audio | executor | 0 | 0.3 | B1 | sonnet | 1 file + 3 tests |
| B3 | harden-mcp | executor | 0 | 0.4, 0.5 | B1, B2 | sonnet | 1 file + 3 tests |
| B4 | swarm-core | executor | 1 | 1.1, 1.2, 1.3 | none (after batch 0) | opus | 3 new files + 1 modified |
| B5 | swarm-test-dash | executor | 1 | 1.4, 1.5 | B4 | sonnet | 1 rewrite + 1 dashboard edit |
| B6 | memory-layer | executor | 2 | 2.1, 2.2, 2.3 | none (after batch 1) | sonnet | 4 new files + 1 modified |
| B7 | lessons-skills | executor | 2 + 3 | 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5 | none (after B6) | sonnet | 7 new files + 4 test files |
| B8 | evolution-cron | executor | 4 | 4.1, 4.2, 4.3 | none (after B7) | sonnet | 2 new files + 1 modified + 2 test files |
| B9 | smart-mascot | executor | 5 | 5.1, 5.2, 5.3, 5.4, 5.5 | B8 | sonnet | 6 new files + 2 modified + 3 test files |
| C1 | spec-auditor | auditor | 6 | (reads only) | all of C2/C3/C4 | sonnet | report only |
| C2 | test-auditor | auditor | 6 | (reads + runs) | all of C1/C3/C4 | sonnet | report only |
| C3 | perf-auditor | auditor | 6 | (reads + runs) | all of C1/C2/C4 | haiku | report only |
| C4 | docs-auditor | auditor | 6 | (reads only) | all of C1/C2/C3 | sonnet | report only |
| W1 | wrap | executor | 6 | (wrap) | none (after C1-C4) | haiku | git + docs |

**Concurrency slots:** `delegation.max_concurrent_children = 4` per
Hermes config. Batch sizing respects this.

**Batch schedule:**
- **Batch 0** (1 + 1 + 1 = 3 agents in parallel): B1, B2, B3 → wait
- **Batch 1** (1 + 1 = 2 agents in parallel): B4, B5 → wait
- **Batch 2** (1 + 1 + 1 = 3 agents in parallel): B6, B7 (memory and skills/lessons are tightly coupled, so they run in parallel but B7 reads B6's new types — see "Reads-from" below) — wait
- **Batch 3** (1 + 1 = 2 agents in parallel): B8, B9 → wait
- **Batch 4** (4 auditors in parallel): C1, C2, C3, C4 → wait
- **Batch 5** (1 wrap): W1

**Reads-from (hard dependencies, not "parallel-safe" candidates):**
- B7 reads `vibedump/memory/store.py` from B6 and uses `MemoryStore` /
  `RecallHit`. **B6 must complete before B7 starts.** B7 also creates
  `vibedump/skills/` as a brand-new top-level package, so its *writes*
  don't conflict with B6 even though they're parallel-ish in the plan.
- B8 reads `vibedump/agent/core.py` and inserts a tail block in
  `OpenClaude.run()`. B6 doesn't touch `core.py`, B7 doesn't touch
  `core.py`. Safe.
- B9 reads `vibedump/agent/core.py` (for `OpenClaude` import only, not
  for editing) and `vibedump/mascot_renderer.py` (modifies it). B8
  appends a single block to `core.py` at the end of `OpenClaude.run()`
  using the literal anchor `return AgentResult(` for the Edit. B9 does
  NOT edit `core.py` — it imports it. **Safe in parallel.**
- C1-C4 all read only. Safe in parallel.

---

## 2. Conflict-resolution rules (binding for every agent)

1. **No agent edits a file outside its declared "Owns" column.** If you
   need a cross-file change, file a note in your completion report and
   the wrap agent will queue it as a follow-up commit.
2. **No agent commits.** Wrap agent does the single squash. If you
   `git add` anything, that's a violation — your deliverable is rejected.
3. **No agent modifies `pyproject.toml`** except the wrap agent, which
   adds the new optional-extras groups at the end of the squash.
4. **No agent renames or moves files.** New files only. Move/rename
   work goes in the wrap agent's queue.
5. **No agent removes tests.** If a test is wrong, fix the production
   code so the test passes. If the test is dead, mark it with
   `@pytest.mark.skip(reason="<reason>")` and explain in the completion
   report.
6. **No agent adds third-party deps** beyond `pyproject.toml`'s current
   set + the M10 plan's approved list (sentence-transformers, watchfiles,
   openWakeWord). Anything else → file a note, wrap agent decides.
7. **Shared types live in dedicated modules.** `RecallHit`, `Lesson`,
   `SkillManifest`, `MascotState`, `DumpiPersona`, `SwarmContext`,
   `SwarmResult` are all in their own `schema.py` / `state.py` /
   `persona.py` files under the new top-level packages. Agents that
   produce them own the file; agents that consume them import from it.
8. **All new SSE events go through the existing `EventBus`.** No agent
   invents a parallel notification path.
9. **All new HTTP routes go through `vibedump/app.py:create_app()`.** No
   agent creates a separate FastAPI app.

---

## 3. Counter-squad (Batch 4) — what each auditor actually checks

### C1 — Spec-compliance auditor
**Reads:** every file in the build agents' "Owns" column.
**Runs:** `grep -nE '(api[_-]?key|secret|token|password)[[:space:]]*[:=]'` on
the whole new diff.
**Asserts:**
- Pre-commit secret hook returns 1 (clean) on the new diff.
- Every new public function has a docstring.
- Every new HTTP route has at least one test.
- Every new SQLite table has an idempotent migration in
  `vibedump/database.py` (or whichever module owns the migration).
- Every new agent-runtime path publishes ≥ 1 SSE event.
- No `print(`, no bare `except:`, no `os.system`, no `shell=True` in
  new code.
- All new files are under the declared top-level packages
  (`vibedump/memory/`, `vibedump/skills/`, `vibedump/mascot/`) or in
  the existing modules.
**Output:** a markdown report at `docs/audits/C1_spec.md` with
per-file ✓/✗ + line numbers for any ✗.

### C2 — Test-coverage auditor
**Runs:** `pytest -q --cov=vibedump --cov-report=term-missing`.
**Asserts:**
- Coverage on touched modules ≥ 80% (the project rule from M10 plan).
- No test is skipped without `@pytest.mark.skip(reason=...)`.
- No test imports a real LLM provider (all use scripted / fakes).
- Full suite finishes in <45s.
- 430 (baseline) + new tests all passing.
**Output:** `docs/audits/C2_tests.md` with the coverage table + the
test count + the runtime + any flagged tests.

### C3 — Performance auditor
**Runs:** `pytest -q --durations=20`.
**Asserts:**
- No single test > 5s.
- MCP schema tests < 100ms each.
- Swarm tests < 2s each (they need real timing for the parallelism
  assertions).
- Audio WebSocket tests < 1s each.
- The audio size-cap test < 1.5s (it's the heaviest new test).
**Output:** `docs/audits/C3_perf.md` with the durations table + flagged
slow tests.

### C4 — Documentation auditor
**Reads:** `docs/`, the README, every docstring, the diff of every
modified file.
**Asserts:**
- `M10_PLAN.md` is updated to mark all four pillars shipped (or
  "partially shipped" with the gap listed).
- `V2_ROADMAP.md` no longer claims features that don't exist (e.g.
  "real-time audio streaming" — the new size cap makes it bounded,
  not "real-time" in the WebRTC sense; either rename or add a
  clarifying sentence).
- `V2_PASS_PLAN.md` exists, has a "Status" section at the top.
- `M11_KICKOFF.md` exists, picks up from where v2.0 leaves off.
- All new public functions have a one-line docstring that matches
  the implementation (spot-check 3 random ones).
- `pyproject.toml` is updated to add `[memory]`, `[skills]`, `[mascot]`,
  `[wakeword]` extras, OR a TODO is filed in the wrap queue.
**Output:** `docs/audits/C4_docs.md` with per-doc ✓/✗ + the missing
items list.

---

## 4. Launch block (copy-pasteable)

Run from `/tmp/vibe-dump` (or wherever the local clone lives). The
user has already approved direction. **No further confirmation needed.**

### Pre-flight (you, the parent, do this ONCE before batch 0)

```bash
cd /tmp/vibe-dump
git checkout -b v2-pass
git pull --ff-only  # in case origin has moved
git status -s       # must be clean
.venv/bin/python -m pytest -q  # must show 430 passed
```

If any of those fail, **stop and report** — do not launch the swarm.

### Batch 0 (3 parallel — hardening)

Dispatch in a single tool-call block (3 `delegate_task` calls):

```python
delegate_task(
    goal="""You are the **harden-bugs** build agent for the Vibe-Dump v2 pass.

Read `docs/V2_PASS_PLAN.md` end-to-end, then complete Tasks 0.1 and 0.2.

**Task 0.1 — Fix inverted blueprint validation.**
File: `vibedump/agent_pipeline.py`, `_compile_blueprint()` method.
Bug: `if validate_blueprint(raw): raw = blueprint_template(title)` is inverted.
The fallback should kick in on INVALID output, not valid output. Flip the
condition so the LLM's good output is kept, the template is the fallback.

TDD steps (do these IN ORDER):
1. Read `vibedump/agent_pipeline.py` lines 230-260 to confirm the current
   behaviour. Read `vibedump/schemas.py:validate_blueprint` to understand
   its return value.
2. Add `tests/test_agent_pipeline.py::test_compile_keeps_valid_blueprint`
   and `tests/test_agent_pipeline.py::test_compile_falls_back_to_template_on_invalid`.
   These will FAIL on the current code.
3. Run `pytest tests/test_agent_pipeline.py -v` — confirm the new tests fail
   for the right reason (the wrong blueprint was kept).
4. Flip the condition in `_compile_blueprint`.
5. Run the new tests — confirm they pass.
6. Run `pytest -q` — confirm 430 + new tests all green.

**Task 0.2 — Add `?dump_id=N` to companion routes.**
File: `vibedump/app.py`, `companion_cursor` and `companion_claudecode` GET
handlers. Accept `dump_id: int | None = Query(default=None)`. If provided,
look up that dump; if missing, 404. If absent, fall back to the current
"most recent with a blueprint" behaviour (backwards compat).

TDD steps:
1. Add two tests to `tests/test_companion_api.py`:
   - `test_companion_explicit_dump_id` (provide a known id, get that one)
   - `test_companion_explicit_dump_id_404` (provide a nonexistent id)
2. Confirm they fail on current code.
3. Modify the two GET routes.
4. Confirm the new tests pass.
5. Confirm all existing `test_companion_api.py` tests still pass.

**Constraints (binding):**
- Do NOT commit. Wrap agent does the commit.
- Do NOT modify any other file. Your owns are exactly `vibedump/agent_pipeline.py`,
  `vibedump/app.py`, `tests/test_agent_pipeline.py`, `tests/test_companion_api.py`.
- Do NOT add new third-party deps.
- All test runtime must stay <5s for your new tests.
- Naming policy: no `api_key` / `secret` / `token` / `password` in any new
  identifier. Use `kind`, `name`, `field`, `value`.
- All new public functions get a docstring.
- After the work, run `pytest -q` and report the exact pass count + the
  per-task commit-style summary (since you don't commit, the wrap agent
  uses your summary for the squash message).

**Output format (in your final reply to the parent):**
- Pass count (e.g. "432 passed in 19s")
- Files modified (paths only)
- New test count per file
- Any deviation from the plan, with reason
- Anything you noticed that the next batch needs to know.""",
    toolsets=["file","terminal","skills","web"],
    role="leaf",
)
```

(B2 and B3 are dispatched in the same `delegate_task` call. Full prompts in §5.)

### Batch 1 (2 parallel — swarm)

After batch 0 reports back with green tests, dispatch B4 + B5 in a single
`delegate_task` call. **Do not start batch 1 until batch 0 is green.**

### Batch 2 (2 parallel — memory + skills)

After batch 1, dispatch B6 first (so B7's reads are available), wait, then
B7. Or run them in parallel if you're confident the B6 → B7 dependency
is loose (it isn't — B7 imports `MemoryStore` directly).

**Recommended:** serial within batch 2, parallel across batch 2's
sub-steps. So: B6, wait, B7, wait.

### Batch 3 (2 parallel — evolution + mascot)

B8 + B9 in parallel. Wait for both.

### Batch 4 (4 parallel — counter squad)

C1, C2, C3, C4 all read-only (or read + run for C2/C3). Dispatch in a
single `delegate_task` call. **Wait for all four reports.**

If any counter agent reports ✗, **stop the wrap agent** and surface the
findings to the user. The user decides whether to roll back, patch, or
ship-with-known-gaps.

### Batch 5 (1 wrap — W1)

```python
delegate_task(
    goal="""You are the **wrap** agent for the Vibe-Dump v2 pass.

You do NOT write product code. You:
1. Read all 4 counter-squad reports in `docs/audits/`.
2. If any ✗ is flagged, STOP and report. The user will decide.
3. If all ✓, do the following in order:
   a. `git status -s` — must show only the expected new + modified files.
   b. `pytest -q` — final green check.
   c. `grep -nE '(api[_-]?key|secret|token|password)[[:space:]]*[:=]' vibedump/**/*.py tests/**/*.py`
      — must return exit 1.
   d. Update `pyproject.toml` to add `[memory]`, `[skills]`, `[mascot]`,
      `[wakeword]` extras, each an empty list for now. (The build agents
      used only stdlib + existing deps.)
   e. Update `docs/M10_PLAN.md` to flip the four pillars to "shipped" or
      "partially shipped" with a one-line gap note per partial.
   f. Update `docs/V2_ROADMAP.md` to add a "V2.0 release notes" section.
   g. Create `docs/M11_KICKOFF.md` with: what shipped, what didn't, what's next.
   h. Single squash commit with the 3 lore trailers and no attribution footer.
   i. Push the branch (do NOT merge to master — leave that for the user).
   j. Report the commit hash + the final test count + the per-phase
      acceptance check.

**Commit message format (use git commit -F <<EOF):**
```
feat(v2.0): M9.5 → v2.0 hardening + M10 pillars (memory, skills, evolution, smart mascot)

[concise summary of the squash, built from the build-agent summaries]

Confidence: <from counter reports, default 'medium'>
Scope-risk: broad
Tested: pytest -q -> <count> passed in <s>s
```

**Final reply format:**
- Commit hash
- Push result (branch name)
- Final test count
- One-line verdict per M10 pillar
- Anything that needs a follow-up commit (the M11 backlog)""",
    toolsets=["file","terminal","skills","web"],
    role="leaf",
)
```

---

## 5. Full agent prompts (B1-B9, C1-C4)

### B1 — harden-bugs (Tasks 0.1, 0.2)

**Goal:** Fix the two specific bugs in `vibedump/agent_pipeline.py` and
`vibedump/app.py` that the v2 review flagged. Add the tests. Run green.
**Owns:** `vibedump/agent_pipeline.py`, `vibedump/app.py`,
`tests/test_agent_pipeline.py`, `tests/test_companion_api.py`.
**Reads:** `docs/V2_PASS_PLAN.md` (full), `vibedump/schemas.py`,
`vibedump/agent_pipeline.py`, `vibedump/app.py`.
**Output:** green tests, no commit, summary report.

### B2 — harden-audio (Task 0.3)

**Goal:** Cap the WebSocket audio size, add the size timeout, send JSON
ack frame, add the `_ptt_sweeper` cleanup task.
**Owns:** `vibedump/app.py` (audio WebSocket section only), `tests/test_audio_stream.py`.
**Reads:** `docs/V2_PASS_PLAN.md` (Task 0.3), `vibedump/app.py` (existing
audio code), `vibedump/integrations/audio_capture.py`.
**New env var:** `VIBEDUMP_AUDIO_MAX_BYTES` (default 1_200_000).
**New sweep interval:** 5 min, deletes files > 1 hour old in `PTT_TMP_DIR`.
**Output:** green tests, no commit, summary report.

### B3 — harden-mcp (Tasks 0.4, 0.5)

**Goal:** Make `register_mcp_tool` handle enums + nullable. Make the
`oneOf`/`anyOf`/`$ref` cases raise `NotImplementedError` loudly. Rewrite
the swarm test to assert prompt diffs (the framework; the rewrite
lands in B5).
**Owns:** `vibedump/agent/registry.py`, `tests/test_mcp_integration.py`,
the assertion skeleton in `tests/test_graph_swarms.py` (B5 does the full
rewrite).
**Reads:** `docs/V2_PASS_PLAN.md` (Task 0.4, 0.5), the official MCP JSON
schema spec, an example real-world MCP server schema (pick any from the
`modelcontextprotocol/servers` GitHub org).
**Output:** green tests, no commit, summary report. **For the assertion
skeleton:** add a `test_graph_swarms_asserts_prompt_roles` test that fails
until B5 lands.

### B4 — swarm-core (Tasks 1.1, 1.2, 1.3)

**Goal:** Build the actual swarm. New `vibedump/agent/swarm.py`,
`vibedump/agent/swarm_executor.py`. Wire into `step_listener` as the
new finalize path, with `_compile_blueprint` as the fallback.
**Owns:** `vibedump/agent/swarm.py` (new), `vibedump/agent/swarm_executor.py`
(new), `vibedump/agent_pipeline.py` (the `step_listener` modification
+ new SQLite migration for `swarm_runs`), `vibedump/database.py` (the
`swarm_runs` migration only).
**Reads:** `docs/V2_PASS_PLAN.md` (Phase 1), `vibedump/agent_pipeline.py`,
`vibedump/database.py:_migrate_*` patterns.
**New SSE events:** `swarm.started`, `swarm.node_started`,
`swarm.node_done`, `swarm.node_failed`, `swarm.completed`,
`swarm.failed` (all with `dump_id` + `node` + `status`).
**Output:** green tests (except the rewritten swarm test, which B5 does),
no commit, summary report.

### B5 — swarm-test-dash (Tasks 1.4, 1.5)

**Goal:** Full rewrite of `tests/test_graph_swarms.py`. Add the
Swarm panel to the dashboard HTML.
**Owns:** `tests/test_graph_swarms.py` (full rewrite), `vibedump/static/dashboard.html`.
**Reads:** `docs/V2_PASS_PLAN.md` (Task 1.4, 1.5), `vibedump/agent/swarm.py`
(from B4 — but B4 may not be done when you start, so import with
`pytest.importorskip("vibedump.agent.swarm")` and have the test skip
cleanly if missing).
**New dashboard feature:** collapsible "Swarm" panel under the Agent
panel. Subscribes to the new SSE events.
**Output:** green tests, no commit, summary report.

### B6 — memory-layer (Tasks 2.1, 2.2, 2.3)

**Goal:** Build `MemoryStore`, the `Embedder` protocol + 3 implementations,
and wire `recall()` into `step_listener` before the LLM prompt.
**Owns:** `vibedump/memory/__init__.py` (new), `vibedump/memory/schema.py`
(new), `vibedump/memory/store.py` (new), `vibedump/memory/embeddings.py`
(new), `vibedump/agent_pipeline.py` (the recall-before-prompt hook only),
`tests/test_memory_store.py` (new), `tests/test_memory_embeddings.py`
(new), `tests/test_memory_recall.py` (new).
**Reads:** `docs/V2_PASS_PLAN.md` (Phase 2), existing `vibedump/ragmemory.py`
to avoid duplicating chunk logic, `vibedump/agent_pipeline.py`.
**Note:** The `MemoryStore` is additive — `RagMemory` stays untouched.
The `step_listener` change is a single block: "before building the
prompt, call `self.memory.recall(...)` and append the snippets to the
prompt." Do not remove the existing `self.memory.remember(...)` calls.
**Output:** green tests, no commit, summary report.

### B7 — lessons-skills (Tasks 2.4, 2.5, 3.1-3.5)

**Goal:** Build `LessonExtractor`, all of `vibedump/skills/` (manifest,
sandbox, synthesizer, registry), the demo skill, and all the tests.
**Owns:** `vibedump/memory/lessons.py` (new), `tests/test_memory_lessons.py`
(new), `vibedump/skills/__init__.py` (new), `vibedump/skills/schema.py`
(new), `vibedump/skills/sandbox.py` (new), `vibedump/skills/synthesizer.py`
(new), `vibedump/skills/registry.py` (new),
`vibedump/skills/examples/extract_action_items.py` (new), and the 3 new
test files.
**Reads:** `docs/V2_PASS_PLAN.md` (Phases 2 + 3), `vibedump/memory/store.py`
(from B6), the AST-scanning pattern from `vibedump/integrations/rclone_sync.py`
for reference (it does its own subprocess invocation — don't copy its
ad-hoc error handling, use a proper try/except with typed errors).
**Important:** This is a single big agent. Budget accordingly. The agent
MUST be given `delegate_task` with `acp_command` unset (use the default
Hermes transport) and a long enough effective context window. If the
agent runs out of turns, split it into B7a (lessons) and B7b (skills)
on retry.
**Output:** green tests, no commit, summary report.

### B8 — evolution-cron (Phase 4)

**Goal:** `EvolutionHook` (Stop callback in `OpenClaude.run`),
`scripts/vibedump_evolve.py` (CLI), the systemd timer addition, the tests.
**Owns:** `vibedump/agent/evolution.py` (new), `vibedump/agent/core.py`
(append a single block at the tail of `OpenClaude.run()` using the
literal anchor `return AgentResult(` for the Edit), `scripts/vibedump_evolve.py`
(new), `scripts/install_systemd.sh` (append the timer unit), `tests/test_evolution_hook.py`
(new), `tests/test_evolve_cli.py` (new).
**Reads:** `docs/V2_PASS_PLAN.md` (Phase 4), `vibedump/agent/core.py`
(look for the exact tail of `OpenClaude.run()`), existing systemd unit
files for the format.
**Output:** green tests, no commit, summary report.

### B9 — smart-mascot (Phase 5)

**Goal:** `MascotState`, `DumpiPersona`, `MascotAgent`, `WakewordRouter`,
the HAT compositor changes, all the tests.
**Owns:** `vibedump/mascot/__init__.py` (new), `vibedump/mascot/state.py`
(new), `vibedump/mascot/persona.py` (new), `vibedump/mascot/agent.py`
(new), `vibedump/mascot/wakeword.py` (new), `vibedump/mascot_renderer.py`
(modify, add mood/XP/lesson layers), `vibedump/integrations/whisplay.py`
(modify, mood-driven frame selection), `vibedump/static/dashboard.html`
(modify, "Ask Dumpi" textbox), `data/dumpi_persona.json` (new),
`tests/test_mascot_agent.py` (new), `tests/test_mascot_persona.py`
(new), `tests/test_mascot_wakeword.py` (new).
**Reads:** `docs/V2_PASS_PLAN.md` (Phase 5), `vibedump/agent/core.py`
(for the `OpenClaude` import), `vibedump/mascot_renderer.py`,
`vibedump/integrations/whisplay.py`.
**Note:** Does NOT edit `vibedump/agent/core.py` — imports it only.
**Output:** green tests, no commit, summary report.

### C1 — spec-compliance auditor
**Goal:** Run the 9-point compliance check from §3, produce
`docs/audits/C1_spec.md`.
**Owns:** `docs/audits/C1_spec.md` (new), reads everything else.
**Output:** report only, no commit.

### C2 — test-coverage auditor
**Goal:** Run the 5-point test check, produce `docs/audits/C2_tests.md`.
**Owns:** `docs/audits/C2_tests.md` (new), reads + runs the test suite.
**Output:** report only, no commit.

### C3 — performance auditor
**Goal:** Run the per-test durations check, produce `docs/audits/C3_perf.md`.
**Owns:** `docs/audits/C3_perf.md` (new), reads + runs the test suite.
**Output:** report only, no commit.

### C4 — documentation auditor
**Goal:** Run the doc check, produce `docs/audits/C4_docs.md`.
**Owns:** `docs/audits/C4_docs.md` (new), reads everything.
**Output:** report only, no commit.

---

## 6. Per-agent "definition of done" (all agents must satisfy)

1. **All modified/created files compile / parse cleanly.** `python -c
   "import vibedump.X"` succeeds for every touched module.
2. **`pytest -q` is green** from the agent's worktree, including the
   agent's new tests.
3. **No file outside the agent's "Owns" column is modified.** If the
   agent found a cross-file bug, file it in the completion report and
   leave the file untouched.
4. **All new public functions have a one-line docstring.** All new
   HTTP routes have at least one test in the agent's deliverable.
5. **No `print(`, no bare `except:`, no `shell=True`.** Naming policy
   upheld.
6. **The agent's new tests run in <5s per test.** The agent's new test
   files combined run in <30s.
7. **The agent's completion report includes:**
   - The exact pytest output (last 5 lines, e.g. "442 passed in 24s")
   - The list of files modified/created
   - The list of new test files + test count per file
   - Any deviation from the plan, with the smallest possible justification
   - Anything the next agent in the chain needs to know

---

## 7. Open risks (read before launching)

1. **Pydantic-Graph SQLite persistence.** B4 may need a small custom
   `BaseStatePersistence` if pydantic-graph's built-in `FileStatePersistence`
   doesn't fit. B4 has authority to write that as part of its owns.
2. **bge-small memory pressure.** bge-small int8 takes ~130 MB. On a
   Pi Zero 2 W with 512 MB total, the rest of the app must stay
   below ~250 MB. B6's `PiLocalEmbedder` should be lazy-loaded (only
   when first `embed()` is called) and the `Cache` should hit on the
   first miss, so the cold start is fine. The hot loop is small.
3. **openWakeWord availability.** openWakeWord isn't on PyPI in a
   pip-installable form for ARM. B9 must default to a text-based
   stub (the "default model" in the plan) and gate the real model
   behind a `pip install vibedump[wakeword]` extra that the wrap
   agent adds but does NOT install by default. Documented in
   `M11_KICKOFF.md`.
4. **Inverted validation test may break the M4 test suite.** B1
   must run the *full* suite, not just `test_agent_pipeline.py`, to
   catch the upstream callers that may have been written against the
   buggy behaviour. If any M4 test breaks, B1 fixes the test (with
   a docstring comment explaining "this used to assert the buggy
   behaviour; flipped in v2.0") and reports it in the summary.
5. **Audio cap test flakiness.** B2's size-cap test sends 1.5 MB over
   a WebSocket. The exact frame count matters. Use `b"\x00" * 1024`
   in a 1500-iteration loop, not a single 1.5 MB frame (some
   test clients chunk at 64 KB).

---

## 8. Recovery / abort

If at any point a batch goes red (test failures the agent can't fix in
one retry):

1. The parent stops launching further batches.
2. The parent reads the failing agent's completion report and the
   failing test output.
3. The parent reports the blocker to the user. No silent fixes.
4. The user decides: roll back the failing agent's work, dispatch a
   fix-it agent, or accept the gap and ship v2.0-rc1 with a known-issues
   note.

---

## 9. Time budget (rough)

| Batch | Agents | Wall (sequential sum) | Wall (with parallel) |
|-------|--------|----------------------|----------------------|
| Pre-flight | 0 | 30s | 30s |
| 0 (hardening) | 3 | 45 min | 20 min |
| 1 (swarm) | 2 | 60 min | 35 min |
| 2 (memory + skills) | 2 | 120 min | 70 min |
| 3 (evolution + mascot) | 2 | 90 min | 50 min |
| 4 (counter squad) | 4 | 40 min | 15 min |
| 5 (wrap) | 1 | 10 min | 10 min |
| **Total** | | **~6 hours** | **~3.5 hours** |

Plan for the long one. The user has the Wild Earth QLD case deadline
(8/6/26) and a separate 8/7 conciliation. Don't promise v2.0 ships
today — promise it ships within one focused session.
