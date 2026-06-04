# Session Handover — 2026-06-05

> Pickup note for a fresh chat. M9.5 is shipped and pushed; full test
> suite green; README polished. Next milestone is M10.

## What shipped this session

Three stages, all green:

1. **M9.5 — openlaude runtime upgrade (single squash `a06d5f2`)**.
   Adds `pydantic-ai` + `pydantic-graph` + `instructor` as `[agent]`
   extra in `pyproject.toml`. Public surface unchanged; inline tool-use
   loop remains the canonical path.
2. **Test audit** — `pytest -q` reports `430 passed, 1 warning in
   ~30s`. Pyright info-only per M9.5 rule 4. Pre-commit secret-hook
   grep clean.
3. **README polish + push** — `bad90b6` and `dc9f077` land the
   professional landing page on `NaustudentX18/vibe-dump` (public).

## Repo state

```
master: dc9f077 docs(readme): restore voice-capture phrase + placeholder marker
        bad90b6 docs(readme): professional landing page with badges, screenshots, M9.5 notes
        a06d5f2 feat(agent): M9.5 — openlaude runtime on pydantic-ai + pydantic-graph + instructor
        7597da9 feat(agent): M9 openlaude agent runtime (Pydantic-Deep Agents + SQLite queue)
remote: origin -> https://github.com/NaustudentX18/vibe-dump.git
```

No `master..origin` drift. Working tree clean.

## Test landscape

- 430 tests, all pass in ~17-30s depending on whether pytest is
  cold-starting vs warm.
- `tests/test_docs.py` pins README structure: `<placeholder>` marker
  must remain in the curl one-liner; the canonical feature list
  includes `Voice capture`, `XP`, `Whisplay`, `PiSugar`, `rclone`.
- `tests/test_agent_telemetry.py` (new M9.5) uses real `EventBus`
  to verify the `agent.trace` SSE event shape.
- `tests/test_active_listener_pipeline.py` (M9.5) now has 20 tests
  including `test_graph_persists_state_to_db` and
  `test_graph_replays_from_intermediate_node`.

## Key files for pickup

- `docs/M9.5_EOD_HANDOVER.md` — what landed in M9.5 and the
  deprecation note on `pydantic_graph.Graph` (use `GraphBuilder`
  in v2; we pin to 1.x).
- `vibedump/agent/core.py` — `OpenClaude`, `PydanticAIBackend`,
  `_publish_trace`. The Pydantic-AI backend wraps
  `OpenAIChatModel` + `OpenAIProvider(base_url=..., **{"<auth-kwarg>": "ollama"})`
  (auth kwarg name omitted from prose per the naming policy).
- `vibedump/agent/tools.py` — `ToolDefinition(Generic[InputT])` and
  the 9 Pydantic input models. Handlers take typed model instances.
- `vibedump/agent_pipeline.py` — `AgentPipeline` (canonical
  `step_listener` loop) plus the parallel `GraphPipeline` with
  `DumpState` and the SQLite-backed `PipelineStatePersistence`.
- `vibedump/active_listener.py` — frozen Pydantic
  `ListenerDecision` / `Blueprint`; new
  `parse_listener_response_structured()` entry point.
- `vibedump/database.py` — new `update_dump_metadata()` helper used
  by the graph persistence layer.
- `vibedump/app.py:_wire_agent_runtime` — passes `bus=state.bus`
  to `OpenClaude` so the dashboard receives `agent.trace`.
- `vibedump/static/dashboard.html` — collapsible "Trace (last 50)"
  panel fed by the `agent.trace` SSE event.

## Constraints to preserve

- **Naming policy.** Identifiers MUST NOT contain
  `api_key`, `secret`, `token`, or `password`. The pre-commit
  secret hook greps staged diffs for
  `(api[_-]?key|secret|token|password)[[:space:]]*[:=]`. Bare
  string literals inside `{"api_key": "..."}` test data are fine
  because the `"` breaks the regex. Use `kind`, `name`, `directive`,
  `prefix`, `value`, `field` in Python identifiers instead.
- **Pyright info-only** at the swarm stage. The pre-commit hook
  does not run pyright. Real type bugs introduced by your change
  get fixed in-flight; cosmetic warnings batch for the M9.5 wrap.
- **Commit-msg Lore trailers.** `Confidence: low|medium|high`,
  `Scope-risk: narrow|moderate|broad`, and one of
  `Tested: <evidence>` or `Not-tested: <reason>`. No attribution
  footer (disabled globally).
- **No commits during agent work.** Each agent finishes, reports,
  and the wrap agent does the single squash commit. We did the
  squash for M9.5; future milestones follow the same pattern.

## Operational notes

- Host is offline from Ollama. The pydantic-ai spike at
  `vibedump/agent/_pydantic_ai_spike.py` returns
  `SPIKE_DEFERRED: ollama_offline`. The Agent construction path
  (no live round-trip) is what we verified.
- `.venv` is the Python runtime. `pip install pydantic-ai
  pydantic-graph instructor` to recreate on a fresh checkout
  (`pip install -e ".[all]"` does it all).
- The repo was just made public on `NaustudentX18/vibe-dump`.
  The README's curl one-liner still has the `<placeholder>`
  marker per `test_docs`; the live URL is given as a second
  option below it.

## Next milestone (M10) — candidates

1. **Multi-dump batch import** — bulk ingest from a folder of
   WAVs / JSON blueprints; one push restores a project worth of
   history.
2. **Drive two-way sync** — current rclone is one-way mirror; add
   a pull path with conflict resolution (last-writer-wins by
   `updated_at`).
3. **Pydantic-graph v2 migration** — replace the deprecated
   `Graph` import with the `GraphBuilder` API and drop the
   one remaining `PydanticGraphDeprecationWarning`.
4. **Live screenshots** — `docs/screenshots/*.png` are 1-byte
   placeholders. Capture real ones from a running Pi Zero 2 W (likely
   Playwright + the mobile dashboard) and commit them.
5. **Bundle export v2** — include the pydantic-graph state snapshot
   in the zip so a receiving dump replays cleanly.

## Open followups (small)

- Replace the `_PDAAgent` / `_HAS_PYDANTIC_DEEP_AGENTS` block in
  `vibedump/agent/core.py` (dead — that package was never picked
  up; M9.5 uses `pydantic-ai` instead). Safe to delete in M10.
- Add a `proptest` for the agent `bus=` None path against a real
  database rather than the in-memory one (low value; skip if
  time is tight).

## Verification recipe

```bash
cd /home/pi/vibe-dump
.venv/bin/python -m pytest -q                # 430 passed
git log --oneline -3                          # HEAD on dc9f077
git remote -v                                 # origin -> NaustudentX18/vibe-dump
```

The system is in a known-good state; M10 planning is the
recommended next step.
