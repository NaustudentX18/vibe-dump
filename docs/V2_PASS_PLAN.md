# Vibe-Dump V2 Pass — Implementation Plan

> **For Hermes:** Use subagent-driven-development (parallel swarm) to implement this
> plan. Dispatch 7 build agents in 3 batches (2+3+1+1), then 1 wrap agent.
> See `V2_PASS_SWARM.md` for the launch block.

**Goal:** Take the M9.5 → v2 code and harden it, then ship the missing M10 features
that make Vibe-Dump actually self-improving. By the end, the four M10 pillars are
real, the swarm is a real swarm (parallel + shared state + retries), the WebSocket
audio is production-safe, and the MCP adapter handles real-world schemas.

**Architecture:** No rewrites. M9.5's `OpenClaude`, the event bus, the `ToolRegistry`,
`GraphPipeline`, the agent queue, and the dashboard stay. New work slots into the
existing seams: `MemoryStore` sits next to `RagMemory`; `SkillSandbox` plugs into
`ToolRegistry.register`; `EvolutionHook` is a `Stop` callback in `OpenClaude.run`;
`SmartMascot` is a second `OpenClaude` instance with three tools. The audio WebSocket
and MCP adapter get the fixes I called out in the review, applied as a thin
hardening pass before the new features go in.

**Tech Stack:** Pydantic v2, Pydantic-AI, Pydantic-Graph 1.x (pin), Instructor,
FastAPI, SQLite (FTS5 + WAL), faster-whisper, Pipecat (audio), bge-small-en-v1.5
(embeddings, via sentence-transformers on Pi), openWakeWord (wakeword). All already
in `pyproject.toml` or added by this plan. **No new vendor lock-in.**

**Carry-overs from M9.5 / M10 plan (verbatim, do not relitigate):**
- Naming policy: no `api_key` / `secret` / `token` / `password` in identifiers.
- Pre-commit secret hook must stay clean (`grep` returns 1).
- Pyright info-only at swarm stage.
- Commit trailers: `Confidence:`, `Scope-risk:`, `Tested:` or `Not-tested:`.
- No attribution footer.
- Pydantic-Graph pinned `pydantic_graph<2`.
- 430 existing tests must stay green after every phase.
- The wrap agent does the single squash commit at the end. Build agents don't commit.

**Test discipline:** TDD where it makes sense, but the "wrap a real MCP server in
a fake one" / "ship bge-small and run it on CPU" tests are skipped behind a fixture
and a marker. Every agent adds at least one happy-path test that runs in <1s on a
fresh checkout. The full suite must finish in <45s with the new tests added.

---

## Phase 0 — Hardening (must ship first)

The review I gave you found five concrete bugs / footguns in the v2 code. Fix them
in one phase before the M10 work lands, so the new code sits on a clean base. Four
parallel agents, all read-mostly on disjoint files.

### Task 0.1 — Fix inverted blueprint validation

**File:** `vibedump/agent_pipeline.py`, `_compile_blueprint()` (≈line 240)
**Bug:** `if validate_blueprint(raw): raw = blueprint_template(title)` — the condition
is inverted. If the LLM output is *valid*, we throw it away. The fallback is the
template, the *good* output is the LLM response.
**Test:** `tests/test_agent_pipeline.py` — add `test_compile_keeps_valid_blueprint`
(seed a valid blueprint, assert the LLM result is returned, not the template) and
`test_compile_falls_back_to_template_on_invalid` (seed an invalid blueprint, assert
the template is returned).
**Risk:** Low. The behavioural change is the *intended* behaviour. Some M4 tests
may need their fixtures flipped.

### Task 0.2 — Add `?dump_id=N` to companion routes

**File:** `vibedump/app.py`, `companion_cursor` and `companion_claudecode` (≈line 2700)
**Change:** Accept `dump_id: int | None = Query(default=None)`. If provided, look up
that dump; if missing, 404. If absent, fall back to "most recent with a blueprint"
(current behaviour, kept for backwards compat with whatever already integrated).
**Test:** `tests/test_companion_api.py` — add two tests: explicit dump_id works,
explicit dump_id with no blueprint 404s.
**Risk:** Low. The `0.0.0.0:8080` no-auth posture is unchanged; this just stops the
silent flip.

### Task 0.3 — Cap WebSocket audio size + cleanup

**File:** `vibedump/app.py`, `audio_stream` WebSocket (≈line 2640)
**Changes:**
1. Hard cap: `MAX_STREAM_BYTES = 1_200_000` (~30s at 16 kHz × 2 bytes; matches a
   30-second PTT turn at the existing format). Reject with `1009` (Message Too Big)
   when exceeded.
2. Hard timeout: 60s of silence (no bytes) → close with `1001`.
3. Send a JSON ack frame back to the client after the user turn is appended, with
   `{"type": "ack", "transcript": "...", "audio_path": "..."}`. If the LLM step
   fails, send `{"type": "error", "message": "..."}` and close `1011`.
4. Temp file cleanup: register a single `asyncio` task in `AppState` called
   `_ptt_sweeper` that runs every 5 min and deletes `PTT_TMP_DIR` files older than
   1 hour. Start it in `create_app()`; cancel it on shutdown.
**Test:** `tests/test_audio_stream.py` — add `test_audio_stream_size_cap_rejects`
(stream 1.5 MB, expect 1009), `test_audio_stream_ack_frame` (verify ack frame is
received), `test_ptt_sweeper_deletes_old_files` (touch old file, run sweeper, assert
gone).
**Risk:** Medium. The 30s cap is opinionated. Make it env-configurable
(`VIBEDUMP_AUDIO_MAX_BYTES`, default 1.2 MB) so the user can raise it.

### Task 0.4 — Harden `register_mcp_tool` for real schemas

**File:** `vibedump/agent/registry.py`, `register_mcp_tool` method.
**Changes:**
1. Add `enum` handling: a field with `"enum": [...]` becomes a `Literal[...]`
   Pydantic field.
2. Add nullable: `"type": ["string", "null"]` becomes `str | None`. Also handle
   `"nullable": true` for the older MCP drafts.
3. Add `description` propagation (already there, double-check it's reaching
   `Field(description=...)` — currently the field's `description` is in
   `Field(description=field_desc)` but the *top-level* tool description is the
   MCP `description`. Verify both flow into `tool.input_schema()`).
4. Add a `NotImplementedError` (with a clear message) for `oneOf` / `anyOf` /
   `$ref` so the failure mode is loud, not silent. Don't try to implement these
   — YAGNI. The error message must point at `pydantic`'s `TypeAdapter` for the
   path forward.
**Test:** `tests/test_mcp_integration.py` — add `test_register_mcp_enum_field`
(field is `Literal["GET", "POST", "PUT"]`), `test_register_mcp_nullable_field`
(field is `str | None`), `test_register_mcp_oneof_raises` (raises
`NotImplementedError` with a useful message).
**Risk:** Low. Pure additive, existing tests stay green.

### Task 0.5 — Fix the swarm test to actually test the swarm

**File:** `tests/test_graph_swarms.py` (the current test passes by string-marker).
**Change:** Rewrite the assertions to check *what each LLM call was given*, not
just the final blueprint markers. Use the existing `ScriptedLLM.calls` list to
assert:
- `call[0]` (Architect) was given the transcript and an "architect" prompt role.
- `call[1]` (Critic) was given the *Architect's output* as input.
- `call[2]` (Security) was given the *Critic's output* as input.
This is what makes the test a swarm test rather than a string-substitution test.
**Risk:** Low. May require renaming the LLM call's prompt templates so the role
suffix is grep-able. That's a Phase 1 change, not a Phase 0 change — file the
assertion framework here, the prompt templating is done as part of Task 1.1.

---

## Phase 1 — Real Swarm (the headline)

The current "Architect → Critic → Security" loop is a hand-coded sequence. This
phase turns it into an actual swarm: parallel branches, shared state, retries, a
real DAG. This is the part that makes the v2 commit title honest.

### Task 1.1 — Define the Swarm DAG

**File (new):** `vibedump/agent/swarm.py`
**Shape:**
```python
class SwarmNode(Protocol):
    name: str
    role: Literal["architect", "critic", "security", "tester"]
    async def run(self, ctx: SwarmContext) -> SwarmResult: ...

class SwarmContext(BaseModel):
    dump_id: int
    transcript: str
    prior_outputs: dict[str, str]  # name -> output, for fan-in
    model_config = ConfigDict(frozen=True)

class SwarmResult(BaseModel):
    node: str
    output: str
    confidence: float  # 0..1, the LLM's self-reported confidence
    needs_retry: bool
```
The DAG is defined declaratively as a list of node specs with dependencies:
```python
SWARM_V1: list[NodeSpec] = [
    NodeSpec("architect", "ArchitectNode", depends_on=[]),
    NodeSpec("critic",    "CriticNode",    depends_on=["architect"]),
    NodeSpec("security",  "SecurityNode",  depends_on=["architect"]),
    NodeSpec("tester",    "TesterNode",    depends_on=["critic", "security"]),
]
```
**Why:** Architect runs first (no deps). Critic *and* Security both depend on
Architect, so they can run in parallel. Tester fans in from Critic + Security.
This is a real DAG, not a sequence. The current 3-node pipeline becomes a 4-node
graph with one fan-out and one fan-in.

### Task 1.2 — Swarm executor with parallelism

**File (new):** `vibedump/agent/swarm_executor.py`
**Shape:**
```python
class SwarmExecutor:
    def __init__(self, llm: LLMProvider, max_concurrency: int = 2): ...
    async def execute(self, dump_id: int, spec: list[NodeSpec], ctx: SwarmContext) -> dict[str, SwarmResult]:
        # topo sort spec; for each wave, gather all nodes whose deps are satisfied
        # run them concurrently (asyncio.gather with semaphore=2 for Pi Zero 2 W)
        # persist each result to a new dumps.swarm_runs SQLite table as it lands
        # on retry-needed, re-queue with backoff (max 2 retries per node)
        # return the final dict
```
**Concurrency limit:** `max_concurrency=2` is hard-coded for the Pi Zero 2 W.
4 simultaneous LLM calls would OOM the 512 MB box. The semaphore is exposed
as a kwarg so a desktop run can raise it.
**Persistence:** new SQLite table `swarm_runs (id, dump_id, node, status,
output, confidence, attempts, created_at, completed_at)`. Migrations live in
`vibedump/database.py:_migrate_swarm_runs` (idempotent, runs on `db.initialize()`).

### Task 1.3 — Wire `SwarmExecutor` into `step_listener`

**File:** `vibedump/agent_pipeline.py`, `step_listener()` (≈line 200)
**Change:** When the LLM responds `[FINALIZE]`, instead of calling
`_compile_blueprint` (single LLM call), call `SwarmExecutor.execute(...)`.
The final blueprint is the *Tester's* output (the fan-in node), with
`Critic` and `Security` annotations stitched in as `## Reviewer notes`
sections in the markdown.
**Backwards compat:** Keep `_compile_blueprint` as a fallback. If the swarm
executor raises (e.g. pydantic-graph not installed, LLM timeout), fall
through to the old single-LLM path so the dump can still finalize. The
event bus publishes `swarm.started`, `swarm.node_done`, `swarm.completed`
or `swarm.failed` so the dashboard can render a live graph.

### Task 1.4 — Rewrite the swarm test to actually test the swarm

**File:** `tests/test_graph_swarms.py` (full rewrite)
**Asserts:**
- `state.visited_nodes` includes all 4 nodes in the expected topo order.
- `Critic` and `Security` run *in parallel* (timestamps within 50ms of each
  other, using `time.monotonic()` snapshots in the executor).
- `Tester` only runs after both `Critic` and `Security` are done.
- Retry: feed `Critic` a "needs_retry" response on attempt 1, a clean one on
  attempt 2. Assert `attempts == 2` and the result contains the attempt-2 output.
- A failing node (3 retries exhausted) emits `swarm.failed` and the dump
  falls back to `_compile_blueprint` (the old path). Assert the fallback
  blueprint was written.
- The LLM was called with the right role-suffixed prompt for each node
  (architect / critic / security / tester) — i.e. the test from Task 0.5
  lands here.

### Task 1.5 — Dashboard: live swarm graph panel

**File:** `vibedump/static/dashboard.html` (and any associated JS)
**Add:** A collapsible "Swarm" panel under the existing Agent panel. Subscribes
to the new `swarm.*` SSE events. Renders a small live DAG:
- Nodes: architect (square), critic (circle), security (triangle), tester (diamond)
- Edges: dependency arrows
- Node colour: grey (pending), blue (running), green (done), red (failed)
- A status line per node: "architect: drafting…", "critic: reviewing (attempt 2)..."
- Hidden when the swarm executor isn't being used (i.e. when `_compile_blueprint`
  is the active path).
**No new deps.** Plain HTML + a few lines of vanilla JS hooking the existing
SSE subscription.

---

## Phase 2 — Memory Layer (M10 Pillar 1)

This is what makes the listener answer questions like "we did this last time, didn't
we?" instead of starting from zero every conversation. `RagMemory` already does
FTS5 over chunks; this phase adds embeddings, lessons, and a real `MemoryStore`
API on top.

### Task 2.1 — `MemoryStore` (FTS5 + vec, single API)

**File (new):** `vibedump/memory/__init__.py`, `vibedump/memory/store.py`,
`vibedump/memory/schema.py`
**Shape:**
```python
class RecallHit(BaseModel):
    source: Literal["dump", "blueprint", "turn", "lesson"]
    ref_id: int
    snippet: str
    score: float
    modality: Literal["text", "audio"] = "text"

class MemoryStore:
    def __init__(self, db: Database, embeddings: Embedder | None = None): ...
    def recall(self, query: str, k: int = 5, *, modality: str = "text") -> list[RecallHit]: ...
    def learn(self, *, fact: str, kind: Literal["dump", "blueprint", "turn", "lesson"],
              ref_id: int, embedding: list[float] | None = None) -> None: ...
    def forget(self, ref_id: int, kind: str) -> None: ...
```
`recall` is FTS5-first (BM25), then re-ranks with cosine similarity against the
embedding if the `Embedder` is wired. If no embedder, FTS5 alone is fine for the
dev path. **Existing `RagMemory` stays** — `MemoryStore` is a superset, not a
replacement. The agent queue's `agent.trace` event includes the recall hits so the
Trace panel can show "remembered: 3 relevant dumps".

### Task 2.2 — `Embedder` with Pi-local + PC-offload

**File (new):** `vibedump/memory/embeddings.py`
**Shape:**
```python
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class PiLocalEmbedder:  # bge-small-en-v1.5 int8 via sentence-transformers
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"): ...

class PCOffloadEmbedder:  # bge-m3 via the LAN Ollama
    def __init__(self, base_url: str = "http://desktop-ujsii52.local:11434"): ...

class CachedEmbedder:  # wraps any embedder with an on-disk sqlite cache
    def __init__(self, inner: Embedder, cache_db: Path): ...
```
The factory in `__init__.py` picks PiLocalEmbedder first, falls back to
PCOffloadEmbedder, falls back to None (FTS5-only). bge-small is ~130 MB RAM,
fits in 512 MB alongside the rest of the app. The cache is keyed by
sha256(text) and the model name, so a model swap invalidates automatically.

### Task 2.3 — Wire `MemoryStore` into `step_listener`

**File:** `vibedump/agent_pipeline.py`
**Change:** In `step_listener`, *before* building the prompt, call
`self.memory.recall(transcript, k=3)` and append a "## What I remember" section
to the prompt with the snippets. This is the M10 plan's "lesson-aware listener"
without the lesson extraction yet (that comes in Task 2.4).
**Test:** seed 3 dumps, start a 4th, assert the prompt to the LLM contains
snippets from the prior 3.

### Task 2.4 — `LessonExtractor` (positive + negative)

**File (new):** `vibedump/memory/lessons.py`
**Shape:**
```python
class Lesson(BaseModel):
    text: str
    kind: Literal["positive", "negative"]
    evidence_dump_id: int
    evidence_turn_id: int
    created_at: datetime

class LessonExtractor:
    def __init__(self, llm: LLMProvider): ...
    def extract_positive(self, dump_id: int) -> Lesson | None: ...
    def extract_negative(self, dump_id: int) -> Lesson | None: ...
```
Called from the `Stop` hook (Phase 3) on dump completion. Positive lesson =
"user pattern P led to blueprint B, reuse P"; negative lesson = "listener said
X, user said Y, diff is Z, don't do X again". The extractor asks the LLM to
emit a `Lesson` Pydantic model. On validation failure, regex fallback. On
neither, return `None` — not every dump produces a lesson.
**Storage:** `lessons` table. `lessons_fts` virtual table for keyword recall.
Indexed by `kind`, `created_at`, `evidence_dump_id`.

### Task 2.5 — Memory tests

**File (new):** `tests/test_memory_store.py`, `tests/test_memory_recall.py`,
`tests/test_memory_embeddings.py`, `tests/test_memory_lessons.py`
**Coverage:**
- `recall` orders hits by relevance on a 50-dump fixture (≥ 95% top-1 accuracy).
- `learn` round-trips.
- `Embedder` cache hit doesn't call the inner embedder twice.
- `LessonExtractor` happy path on a seeded transcript, both positive and
  negative flavours.
- `LessonExtractor` returns None on regex fallback when LLM is offline.

---

## Phase 3 — Skill Synthesis (M10 Pillar 2)

The Voyager move: the agent writes its own tools. This is the part where
Vibe-Dump becomes a system that *gets better* with use.

### Task 3.1 — `SkillManifest` + `Skill` dataclass

**File (new):** `vibedump/skills/__init__.py`, `vibedump/skills/schema.py`
**Shape:**
```python
class SkillManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    description: str = Field(min_length=10, max_length=500)
    input_schema: dict  # Pydantic model_json_schema()
    examples: list[dict] = Field(min_length=1, max_length=5)  # [{"input": ..., "output": ...}]
    success_count: int = 0
    failure_count: int = 0
    last_used: datetime | None = None
    created_from_dump_id: int
    sandbox_ok: bool = False

class Skill(BaseModel):
    manifest: SkillManifest
    handler_path: Path  # ~/.vibedump/skills/<name>.py
    handler_module: str  # importable name
```
**Storage:** live skills at `~/.vibedump/skills/<name>.py` (chmod 600, gitignored,
SD-card persistent). Staging at `~/.vibedump/skills/_staging/`. The path is
configurable via `VIBEDUMP_SKILLS_DIR` so the test suite can use `tmp_path`.

### Task 3.2 — `SkillSandbox` (subprocess + rlimits)

**File (new):** `vibedump/skills/sandbox.py`
**Shape:**
```python
class SandboxResult(BaseModel):
    ok: bool
    error: str | None = None
    duration_ms: int
    side_effects: list[str] = []  # files created, network attempts, etc.

class SkillSandbox:
    def __init__(self, *, timeout_s: int = 5, max_memory_mb: int = 64,
                 deny_network: bool = True, allow_paths: list[Path] = []): ...
    def run(self, skill_path: Path, input_payload: dict) -> SandboxResult: ...
```
The sandbox runs the skill as a Python subprocess with:
- `resource.RLIMIT_AS` capped at `max_memory_mb` MB.
- `subprocess` env stripped of `PATH` (only `/usr/bin/python3`), no `HOME`.
- Static AST scan: reject any module that imports `socket`, `urllib`,
  `http`, `subprocess`, `os.system`, `popen`, or `ctypes`. Fail loud.
- For each example in the manifest, run the skill with that input, capture
  the output, compare structurally (Pydantic `model_validate` against the
  output schema). 3/5 examples pass → promote from staging to live.

### Task 3.3 — `SkillSynthesizer` (LLM turns patterns into skills)

**File (new):** `vibedump/skills/synthesizer.py`
**Shape:**
```python
class SkillSynthesizer:
    def __init__(self, llm: LLMProvider, sandbox: SkillSandbox,
                 skills_dir: Path): ...
    async def synthesize(self, *, pattern: str, examples: list[dict],
                         source_dump_id: int) -> Skill | None: ...
```
Called by the `Stop` hook (Phase 3 evolution, but the synthesizer lives
here). Given a pattern like "extract action items from a transcript" and
3+ example `(input, output)` pairs, asks the LLM to emit Python source code
+ a `SkillManifest`. Writes to staging, runs sandbox, promotes on pass.

### Task 3.4 — `SkillRegistry` (hot-reload into `ToolRegistry`)

**File (new):** `vibedump/skills/registry.py`
**Shape:**
```python
class SkillRegistry:
    def __init__(self, skills_dir: Path, sandbox: SkillSandbox,
                 tool_registry: ToolRegistry): ...
    def discover(self) -> list[Skill]: ...  # scan skills_dir, parse manifests
    def hot_reload(self) -> int: ...       # returns # of new/updated skills
    def register_with(self, tool_registry: ToolRegistry) -> None: ...
    def watch(self) -> None: ...           # watchfiles-based hot-reload loop
```
`discover()` parses the manifest from the .py file's docstring (a structured
JSON docstring convention — `_SKILL_MANIFEST = {...}`) so a single drop in
`~/.vibedump/skills/` is enough to register. `hot_reload()` diffs against the
in-memory set and calls `tool_registry.register_mcp_tool` (we already have
the schema adapter!) for each new/changed skill.
**Backwards compat:** the existing `ToolRegistry.register_mcp_tool` from
M9.5 is the registration primitive. The skill layer is pure plumbing on top.

### Task 3.5 — Skill tests

**File (new):** `tests/test_skill_sandbox.py`, `tests/test_skill_synthesis.py`,
`tests/test_skill_registry.py`
**Coverage:**
- Sandbox rejects a skill that imports `socket` (AST scan catches it before
  the subprocess runs).
- Sandbox rejects a skill that writes outside the allow-list (file is rolled
  back; result.ok = False).
- Sandbox memory cap: a skill that allocates a 200 MB list is killed with
  `MemoryError`, not a segfault.
- Synthesizer promotes a skill after 3/5 examples pass; rejects after 0/5.
- Registry hot-reload: drop a new .py into a tmp skills dir, call
  `hot_reload()`, assert it appears in the `ToolRegistry.schemas()`.
- A skill with a malformed manifest raises `ValidationError` and is left
  in staging (never auto-promoted).

---

## Phase 4 — Evolution Hooks (M10 Pillar 3)

The agent has no idea it made a mistake. This phase gives it the
infrastructure to learn.

### Task 4.1 — `EvolutionHook` (Stop callback in `OpenClaude.run`)

**File (new):** `vibedump/agent/evolution.py`
**Shape:**
```python
class EvolutionHook:
    def __init__(self, memory: MemoryStore, lesson_extractor: LessonExtractor,
                 skill_synthesizer: SkillSynthesizer, bus: EventBus): ...
    async def on_dump_completed(self, dump_id: int, blueprint: str) -> EvolutionOutcome: ...
    async def on_dump_edited(self, dump_id: int, old_blueprint: str,
                              new_blueprint: str) -> EvolutionOutcome: ...
    async def on_dump_deleted(self, dump_id: int, blueprint: str) -> EvolutionOutcome: ...
```
`on_dump_completed` is the happy path: extract a positive lesson, publish
`agent.evolution` with `{kind: "positive", text: "...", dump_id: ...}`.
`on_dump_edited` diffs old vs new, extracts a corrective lesson, patches the
manifest examples of the relevant skill.
`on_dump_deleted` flags the pattern as negative, searches prompt templates
for the offending heuristic, emits a guard-clause patch into
`.omc/patches/evolution-guard-<timestamp>.patch` (text, not applied — for
human review).
**Wired into:** `vibedump/agent/core.py`, at the END of `OpenClaude.run()`.
The hook fires *after* the run, never blocks the run, never raises into the
caller. Failures are logged to the bus as `agent.evolution_failed`.

### Task 4.2 — `scripts/vibedump_evolve.py` (weekly cron)

**File (new):** `scripts/vibedump_evolve.py`
**CLI:** `python -m scripts.vibedump_evolve [--dry-run]`
**Jobs:**
1. Consolidate near-duplicate lessons (cosine > 0.92 → keep higher
   `success_count`, delete the other).
2. Prune lessons with `last_used_at` older than 90 days.
3. Re-embed the full corpus if the configured embedder's model name
   differs from the cache's recorded model.
4. Emit `data/vibe_score.json`: `success_rate`, `mean_recall_precision`,
   `top_5_most_reused_skills`, `top_3_freshest_lessons`.
5. Optionally post a digest to the dashboard's Storage panel via the
   `agent.evolution_digest` SSE event.
**Cron registration:** appended to `scripts/install_systemd.sh` (which
already exists; the timer unit is `vibedump-evolve.timer` running
`Sun 03:17 local`).

### Task 4.3 — Evolution tests

**File (new):** `tests/test_evolution_hook.py`, `tests/test_evolve_cli.py`
**Coverage:**
- `on_dump_completed` extracts a positive lesson, publishes the event.
- `on_dump_edited` diffs the two blueprints, extracts a corrective lesson
  with the smallest-edit summary.
- `on_dump_deleted` flags the pattern negative and writes a guard-clause
  patch file.
- `vibedump_evolve` consolidates two cosine-0.95 lessons into one.
- `vibedump_evolve --dry-run` reports the actions it would take but
  doesn't mutate the DB.

---

## Phase 5 — Smart Dumpi (M10 Pillar 4)

The mascot becomes a live agent with its own memory access, persona, and
wakeword.

### Task 5.1 — `MascotState` + `DumpiPersona`

**File (new):** `vibedump/mascot/state.py`, `vibedump/mascot/persona.py`
**Shape:**
```python
class MascotState(BaseModel):
    mood: Literal["idle", "curious", "working", "proud"]
    xp_level: int
    last_lesson: str | None
    recent_recall: list[str] = Field(max_length=5)  # last 5 RecallHit summaries
    model_config = ConfigDict(frozen=True)

class DumpiPersona(BaseModel):
    tone: str = "terse + dry wit"
    banned_phrases: list[str] = Field(default_factory=list)
    hat_word_budget: int = 12
    dashboard_word_budget: int = 60
    voice_id: str = "default"
```
Persona loaded from `data/dumpi_persona.json` on startup. The dashboard
gets a tiny editor for it (stretch goal — deferred to a v2.1 pass).

### Task 5.2 — `MascotAgent` (second `OpenClaude` instance)

**File (new):** `vibedump/mascot/agent.py`
**Shape:**
```python
class MascotAgent:
    def __init__(self, bus: EventBus, memory: MemoryStore, persona: DumpiPersona,
                 llm: LLMProvider): ...
    async def recall_and_respond(self, query: str, surface: Literal["hat", "dashboard"]) -> str: ...
```
Three tools exposed via a private `ToolRegistry`:
- `mascot.recall(query)` — returns top-3 `RecallHit` snippets, capped to
  `persona.dashboard_word_budget` words.
- `mascot.dumpi_says(text)` — TTS playback, also returns the text.
- `mascot.xp_award(reason)` — calls `db.grant_xp(...)`, publishes `profile.xp`.
The system prompt is built from the persona, the current `MascotState`, and
the most recent 3 lessons. Output is post-processed to enforce the word
budget (truncate on the last complete sentence within budget).

### Task 5.3 — Wakeword routing

**File (new):** `vibedump/mascot/wakeword.py`
**Shape:**
```python
class WakewordRouter:
    def __init__(self, model_name: str = "hey_dumpi_v0",
                 threshold: float = 0.6): ...
    def is_wakeword(self, pcm_chunk: bytes) -> bool: ...
```
Uses openWakeWord's tiny "hey_dumpi" custom model (we train one in P0; the
shipped default is a placeholder that accepts "hey dumpi" as text and
rejects everything else, with a TODO). When the audio capture path
detects the wakeword, the next utterance is routed to `MascotAgent`
instead of the listener pipeline. The decision is published as
`mascot.wakeword_detected` so the dashboard can show "Dumpi is listening..."
instead of "creating dump...".

### Task 5.4 — Mascot integration into the HAT

**File:** `vibedump/integrations/whisplay.py`, `mascot_renderer.py`
**Change:** A new compositor layer that takes the `MascotState` and draws:
- Mood pose (4 base poses for the 4 moods).
- XP ring around the mascot (drawn from `xp_level`).
- Lesson ticker (the 1-line summary of `last_lesson`, scrolling if too long).
The existing `dump.status` → mood mapping stays as the *default* mood
(thinking when the agent is mid-step, etc.), but the mascot can override
to `proud` after a lesson, `curious` when it has an unanswered question, etc.
**No new deps.** PIL only.

### Task 5.5 — Smart Dumpi tests

**File (new):** `tests/test_mascot_agent.py`, `tests/test_mascot_persona.py`,
`tests/test_mascot_wakeword.py`
**Coverage:**
- `MascotAgent.recall_and_respond("hat")` returns ≤ 12 words.
- `MascotAgent.recall_and_respond("dashboard")` returns ≤ 60 words.
- `recall_and_respond` returns "I don't know" (or persona equivalent) when
  `MemoryStore` returns no hits.
- Persona `banned_phrases` are stripped from the response.
- `WakewordRouter.is_wakeword` returns True on the seeded audio chunk,
  False on noise. (The default model is the text-based stub; the real
  openWakeWord path is exercised only when the optional `[wakeword]`
  extra is installed.)
- HAT compositor draws a frame for each mood without raising.

---

## Phase 6 — Counter Squad (the safety net)

The user asked for a counter / audit squad. This phase is the final guard:
independent verification that what the swarm shipped actually works, before
the wrap commit goes in.

### Task 6.1 — Spec-compliance auditor

**Reads:** every file touched by Phases 0–5.
**Asserts:**
- All 7 pre-commit rules in `VIBE_DUMP_BUILD_HANDOVER_2026-06-04-EOD.md`
  still pass (naming policy, no auth-key in code, etc.).
- Every new public function has a docstring.
- Every new HTTP route has a matching test.
- Every new SQLite table has a migration that's idempotent.
- Every new agent-runtime path publishes an SSE event.
- No `print(`, no bare `except:`, no `os.system` in new code.

### Task 6.2 — Test-coverage auditor

**Runs:** `pytest -q --cov=vibedump --cov-report=term-missing`.
**Asserts:**
- Coverage on touched modules is ≥ 80% (project rule from M10 plan).
- No test is skipped without a marker (`@pytest.mark.skip` + reason).
- No test imports a real LLM provider (all use scripted / fakes).
- The full suite finishes in <45s with the new tests added.
- 430 + new tests are all passing (counted and reported).

### Task 6.3 — Performance auditor

**Measures:** on a fresh `pytest` run, capture the per-test runtime.
**Asserts:**
- No single test takes > 5s.
- The MCP schema tests < 100ms each (they're pure-Python).
- The swarm tests < 2s each (the parallelism assertions need real timing).
- The audio WebSocket tests < 1s each.

### Task 6.4 — Documentation auditor

**Reads:** `docs/`, the README, every docstring.
**Asserts:**
- `M10_PLAN.md` is updated to mark all four pillars as "shipped" (or
  "partially shipped" with explicit gaps).
- `V2_ROADMAP.md` no longer claims "real-time audio streaming" without
  the size cap (or the doc is updated to match the new behaviour).
- A new `M11_KICKOFF.md` exists, picking up where v2.0 leaves off.
- The pre-commit secret hook still passes on the whole new diff.

---

## Verification recipe (per-phase)

```bash
cd /home/pi/vibe-dump
.venv/bin/python -m pytest -q                                # 430 + new
.venv/bin/python -m pytest -q tests/test_swarm*.py            # Phase 1
.venv/bin/python -m pytest -q tests/test_memory_*.py          # Phase 2
.venv/bin/python -m pytest -q tests/test_skill_*.py           # Phase 3
.venv/bin/python -m pytest -q tests/test_evolution_*.py       # Phase 4
.venv/bin/python -m pytest -q tests/test_mascot_*.py          # Phase 5
.venv/bin/python -m scripts/vibedump_evolve --dry-run         # Phase 4 cron
grep -nE '(api[_-]?key|secret|token|password)[[:space:]]*[:=]' vibedump/**/*.py
# exit 1 expected (no matches)
git log --oneline -3                                          # confirm squash + lore trailers
```

---

## File-level map (new + modified)

**New files (~3,500 LOC including tests):**
```
vibedump/agent/swarm.py                # SwarmNode, SwarmContext, NodeSpec
vibedump/agent/swarm_executor.py       # topo-sort, parallel gather, retry
vibedump/agent/evolution.py            # EvolutionHook
vibedump/memory/__init__.py
vibedump/memory/schema.py              # RecallHit, Lesson
vibedump/memory/store.py               # MemoryStore
vibedump/memory/embeddings.py          # PiLocalEmbedder, PCOffloadEmbedder, CachedEmbedder
vibedump/memory/lessons.py             # LessonExtractor
vibedump/skills/__init__.py
vibedump/skills/schema.py              # SkillManifest, Skill
vibedump/skills/sandbox.py             # SkillSandbox
vibedump/skills/synthesizer.py         # SkillSynthesizer
vibedump/skills/registry.py            # SkillRegistry (hot-reload)
vibedump/skills/examples/extract_action_items.py
vibedump/mascot/__init__.py
vibedump/mascot/state.py               # MascotState
vibedump/mascot/persona.py             # DumpiPersona
vibedump/mascot/agent.py               # MascotAgent
vibedump/mascot/wakeword.py            # WakewordRouter
scripts/vibedump_evolve.py
tests/test_swarm_dag.py
tests/test_swarm_executor.py
tests/test_swarm_persistence.py
tests/test_memory_store.py
tests/test_memory_recall.py
tests/test_memory_embeddings.py
tests/test_memory_lessons.py
tests/test_skill_sandbox.py
tests/test_skill_synthesis.py
tests/test_skill_registry.py
tests/test_evolution_hook.py
tests/test_evolve_cli.py
tests/test_mascot_agent.py
tests/test_mascot_persona.py
tests/test_mascot_wakeword.py
data/dumpi_persona.json
docs/V2_PASS_PLAN.md                   # this file
docs/V2_PASS_SWARM.md                  # the launch block
docs/M11_KICKOFF.md                    # pickup for v2.1
```

**Modified files:**
```
vibedump/agent_pipeline.py             # _compile_blueprint fix, swarm wiring, recall-before-prompt
vibedump/agent/registry.py             # MCP schema hardening
vibedump/agent/core.py                 # EvolutionHook in OpenClaude.run() tail
vibedump/app.py                        # audio WebSocket hardening, ?dump_id on companion routes
vibedump/database.py                   # swarm_runs + lessons migrations
vibedump/mascot_renderer.py            # mood + XP ring + lesson ticker layers
vibedump/integrations/whisplay.py      # mood-driven frame selection
vibedump/static/dashboard.html         # Swarm panel, Ask-Dumpi textbox
pyproject.toml                         # [memory], [skills], [mascot], [wakeword] extras
docs/V2_ROADMAP.md                     # mark phases shipped
docs/M10_PLAN.md                       # mark pillars shipped (or partial)
scripts/install_systemd.sh             # add vibedump-evolve.timer
```

---

## Constraints (apply to every agent)

1. **No new third-party pip deps** beyond what's in `pyproject.toml` or what the
   M10 plan already calls for (bge-small, openWakeWord, sentence-transformers,
   watchfiles). Anything else requires explicit approval in the agent's
   completion report.
2. **No breaking changes to the M9.5 public API.** `OpenClaude`, `JobQueue`,
   `ToolRegistry`, `ToolDefinition`, the 9 tools, all HTTP routes, all
   dashboard sections stay. New work is additive.
3. **Pre-commit secret hook** stays clean. `grep` returns 1 on the whole
   new diff.
4. **Pyright info-only** at the swarm stage. Fix real type bugs in the
   agent that introduced them; batch minor warnings for the wrap.
5. **Commit trailers** required on the wrap commit only: `Confidence:`,
   `Scope-risk:`, `Tested:`. Build agents do NOT commit.
6. **No attribution footer** on any commit.
7. **Don't commit during agent work.** Wrap agent does the single squash.
8. **All new public functions have docstrings.** All new HTTP routes have
   matching tests in the same agent's deliverable. All new SQLite tables
   have idempotent migrations in the same agent's deliverable.
9. **Test runtime budget:** the full suite must finish in <45s with the
   new tests added. If a single test takes >5s, that agent's deliverable
   is rejected and must be split.
10. **No new top-level packages** beyond `vibedump/memory/`, `vibedump/skills/`,
    `vibedump/mascot/`. Everything else nests into existing modules.
