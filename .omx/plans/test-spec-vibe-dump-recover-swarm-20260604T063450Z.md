# Test Spec: Vibe-Dump Recover Swarm + Milestone 0/1

## Runtime recovery checks
- `omx team status build-vibe-dump-miles-58f07308 --json --tail-lines 120` shows dead workers/pending tasks for the failed first team.
- Inspect worktrees: no worker commits/changes were produced.
- If relaunching a debugger team, verify startup with `omx team status <team> --json` and terminal task states before shutdown.

## Product checks
Run from `/home/pi/vibe-dump`:
- `python -m pytest -q`
- `python -c "import vibedump; from vibedump.database import Database; from vibedump.ragmemory import RagMemory"`
- SQLite behavior tests must verify:
  - WAL mode enabled.
  - Foreign keys enabled.
  - FTS5 virtual table exists and search returns expected fixture content.
  - Dump deletion cascades related turns/blueprints/chunks/FTS rows.
  - Blueprint markdown uses required handover sections.

## Non-goals for tests
- No hardware smoke commands.
- No API-key-backed cloud provider calls.
- No network-dependent tests.
