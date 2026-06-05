# Vibe-Dump V2 Pass — Session Handover (2026-06-05)

> **Status:** Plan and swarm launch block are **shipped and pushed** to
> `NaustudentX18/vibe-dump` master. **Swarm has NOT been launched yet.**
> User asked for a clean handover so a fresh chat can pick it up and
> dispatch the build agents in one go.

**Commit hash:** `d451338`
**Branch:** `master` (clean, ahead of `9f061b1` by 1 commit)
**New docs in repo:**
- `docs/V2_PASS_PLAN.md` — 6 phases, 30+ tasks, full TDD specs
- `docs/V2_PASS_SWARM.md` — 9 build agents + 4 counter auditors

---

## TL;DR for the fresh chat

The v2 commit (`9f061b1`) shipped 4 thin slices (real-time audio WebSocket,
MCP adapter, "swarm" that was actually a hand-coded sequence, IDE companion
routes). The user asked me to design a **full v2 pass** — real hardening +
the missing M10 features — and to lay it out as a swarm they can launch.

I wrote a 6-phase plan + a 9-agent parallel swarm with a 4-agent counter
squad for independent verification, and a wrap agent for the final squash
commit. Everything is committed, pushed, and the swarm doc has copy-pasteable
prompts for every agent.

**Wall time with full parallelism: ~3.5 hours. Sequential: ~6 hours.**

---

## What was done in this session

1. **Reviewed the v2 commit** by reading the actual source on GitHub
   (the README oversells the diff). Found 5 concrete issues:
   - Inverted blueprint validation in `_compile_blueprint`
   - `companion_cursor` / `companion_claudecode` silently flip to latest dump
   - WebSocket audio has no size cap, no auth, no ack frame, no cleanup
   - `register_mcp_tool` silently maps `oneOf`/`anyOf`/`$ref` to `Any`
   - "Multi-agent swarm" test passes by string-marker, not by prompt diff

2. **Read the M10 plan + M9.5 EOD handover + V2_ROADMAP + ARCHITECTURE**
   to make sure the new plan dovetails with what's already committed. The
   M10 four pillars (memory / skills / evolution / smart mascot) are the
   right shape; the v2 commit was a small UI/network step that touched
   3 source files + 4 tests (~530 lines). The "level up" is the M10 work
   plus the 5 hardening fixes.

3. **Loaded the `swarm-execution` skill and the `writing-plans` skill**
   to make sure the plan format matches the project's TDD / subagent-
   driven-development conventions, and the swarm format matches the
   `M9.5 swarm run` that the user already used to ship the openlaude
   upgrade.

4. **Wrote `docs/V2_PASS_PLAN.md`** — 6 phases, file-level map, hard
   constraints, TDD steps per task, acceptance criteria, verification
   recipe. Total ~800 lines.

5. **Wrote `docs/V2_PASS_SWARM.md`** — agent matrix with owns / reads /
   parallel-with / model / budget, conflict-resolution rules (binding),
   counter-squad design (4 auditors with hard gates), copy-pasteable
   launch block with the pre-flight + batch schedule, per-agent
   "definition of done", open risks, recovery protocol. ~600 lines.

6. **Committed and pushed** to `NaustudentX18/vibe-dump` master. Commit
   message has the 3 lore trailers. No attribution footer.

---

## What's next (for the fresh chat)

The user wants to launch the swarm in a new chat. The fresh chat should:

1. Read `docs/V2_PASS_PLAN.md` end-to-end.
2. Read `docs/V2_PASS_SWARM.md` end-to-end.
3. Run the pre-flight from §4 of the swarm doc:
   ```bash
   cd /tmp/vibe-dump          # or wherever the local clone lives
   git checkout -b v2-pass
   git pull --ff-only
   git status -s               # must be clean
   .venv/bin/python -m pytest -q   # must show 430 passed
   ```
4. Dispatch batch 0 (3 parallel build agents: B1, B2, B3).
5. Wait for all 3 to report green.
6. Proceed through batches 1 → 2 → 3 → 4 → 5 per the schedule in §1
   of the swarm doc.

The user explicitly said they want the swarm launched in a fresh chat,
not here. Don't pre-empt — the handover is the deliverable for this
session.

---

## Batch schedule (cheat sheet)

| Batch | Agents | Wall (parallel) | Notes |
|-------|--------|-----------------|-------|
| Pre-flight | 0 | 30s | parent runs once |
| 0 (hardening) | B1, B2, B3 | 20 min | 3 parallel |
| 1 (swarm) | B4, B5 | 35 min | 2 parallel |
| 2 (memory + skills) | B6 → B7 | 70 min | **serial** — B7 imports B6's types |
| 3 (evolution + mascot) | B8, B9 | 50 min | 2 parallel |
| 4 (counter squad) | C1, C2, C3, C4 | 15 min | 4 parallel auditors, hard gates |
| 5 (wrap) | W1 | 10 min | single squash commit |
| **Total** | | **~3.5 hours** | sequential sum ~6h |

**Hard gates:** if any counter auditor (C1-C4) reports ✗, the wrap agent
stops and surfaces to the user. No silent "looks good enough" merges.

---

## File ownership matrix (the binding rule)

Every build agent in `V2_PASS_SWARM.md` §5 has an explicit "Owns"
column. **No agent edits a file outside its owns.** If an agent needs a
cross-file change, it files a note in its completion report and the wrap
agent queues it as a follow-up commit.

The critical parallel-safe claims:
- B6 + B7 are **serial** because B7 reads `vibedump/memory/store.py`
  (B6) and uses `MemoryStore` / `RecallHit` directly.
- B8 appends a tail block to `vibedump/agent/core.py` (the `OpenClaude.run`
  end). B9 imports `core.py` but does **not** edit it. Parallel-safe.
- All C1-C4 auditors are read-only or read+run. Parallel-safe.
- W1 wrap agent runs solo, after C1-C4 all green.

---

## Constraints carried forward (verbatim, do not relitigate)

From M9.5 EOD + M10 plan:
- Naming policy: no `api_key` / `secret` / `token` / `password` in
  identifiers. Use `kind`, `name`, `field`, `value`, `prefix`.
- Pre-commit secret hook must stay clean (`grep` returns 1).
- Pyright info-only at the swarm stage.
- Commit trailers: `Confidence:`, `Scope-risk:`, `Tested:` or
  `Not-tested:`. No attribution footer.
- Pydantic-Graph pinned `pydantic_graph<2`.
- 430 existing tests must stay green after every phase.
- Build agents do NOT commit. Wrap agent does the single squash.
- All new public functions get a docstring. All new HTTP routes get a
  test. All new SQLite tables get an idempotent migration.

Added by v2 pass plan:
- No new third-party pip deps beyond what M10 already approved
  (sentence-transformers, watchfiles, openWakeWord).
- No new top-level packages beyond `vibedump/memory/`,
  `vibedump/skills/`, `vibedump/mascot/`.
- Test runtime budget: full suite <45s, single test <5s.

---

## Key file pointers (for the fresh chat)

**Read first, in this order:**
1. `docs/V2_PASS_PLAN.md` — the source of truth for what to build
2. `docs/V2_PASS_SWARM.md` — the source of truth for how to build it
3. `docs/M10_PLAN.md` — the original M10 plan this v2 pass executes on
4. `docs/M9.5_EOD_HANDOVER.md` — what shipped in M9.5 (the openlaude
   upgrade that v2 sits on top of)
5. `docs/ARCHITECTURE.md` — system diagram, module map, state machine

**Touched by v2 (commit `9f061b1`):**
- `vibedump/agent/registry.py` — `register_mcp_tool` (needs hardening per
  B3)
- `vibedump/agent_pipeline.py` — inverted bug in `_compile_blueprint`
  (B1)
- `vibedump/app.py` — audio WebSocket (B2) + companion routes (B1)

**Untouched but referenced by the new plan:**
- `vibedump/agent/core.py` — B8 appends a single block at the tail of
  `OpenClaude.run()` using the literal anchor `return AgentResult(`.
- `vibedump/database.py` — B4 adds `swarm_runs` migration, B6 adds
  `lessons` migration. Both idempotent, both go in `db.initialize()`.
- `vibedump/ragmemory.py` — **stays untouched**. `MemoryStore` is
  additive, not a replacement.

---

## Open risks the fresh chat should know about

1. **Pydantic-Graph SQLite persistence.** B4 may need a small custom
   `BaseStatePersistence` if the built-in `FileStatePersistence` doesn't
   fit. B4 has authority to write that.
2. **bge-small memory pressure.** ~130 MB. Pi Zero 2 W has 512 MB total.
   B6 must lazy-load the embedder and use the cache on the first miss.
3. **openWakeWord pip availability.** Not pip-installable on ARM as a
   single package. B9 defaults to a text-based stub; real model is gated
   behind `[wakeword]` extra that the wrap agent adds but doesn't install.
4. **Inverted validation test may break M4 tests.** B1 must run the
   *full* suite, not just `test_agent_pipeline.py`. If an M4 test breaks,
   B1 fixes the test (with a comment: "this used to assert the buggy
   behaviour; flipped in v2.0") and reports it in the summary.
5. **Audio cap test flakiness.** B2's size-cap test sends 1.5 MB over
   a WebSocket. Use `b"\x00" * 1024` in a 1500-iteration loop, not a
   single 1.5 MB frame (test clients chunk at 64 KB).

---

## What I did NOT do (and shouldn't do in this chat)

- Did **not** launch the swarm. The user asked for a clean handover +
  new chat. Dispatching build agents here would burn through the case-
  prep time without a payoff (the user is between the FWC conciliation
  prep and the QHRC complaint deadline).
- Did **not** create a `v2-pass` branch. The plan doc is on master; the
  fresh chat will branch from master.
- Did **not** open a PR. The user has not asked for one. The v2 pass
  repo is theirs; the upstream `superpowers` is a different repo
  entirely (the AGENTS.md from `projects/AGENTS.md` is a red herring —
  it's the superpowers contributor guidelines, not vibe-dump's).

---

## Session-end protocol

- [x] Handover doc written (this file)
- [x] Memory updated (next step)
- [x] No build agents dispatched
- [x] Repo is clean (1 commit ahead of v2, both pushed)
- [x] Plan and swarm are accessible in the new docs folder
- [x] Fresh chat has everything it needs to launch in one go
