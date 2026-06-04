# PRD: Recover Vibe-Dump Swarm and Deliver Milestone 0/1

## Objective
Recover from the failed OMX team launch and deliver the first Vibe-Dump implementation scope from `/home/pi/VIBE_DUMP_BUILD_HANDOVER_2026-06-04.md`.

## In scope
- Diagnose why team `build-vibe-dump-miles-58f07308` failed.
- Preserve evidence of the failure.
- Relaunch a debugging/delivery team if the runtime can be made reliable.
- If team runtime remains unreliable, continue with direct/natively delegated implementation without blocking.
- Build `/home/pi/vibe-dump` Milestone 0 and 1 only: scaffold, config examples, README, fake providers/hardware, SQLite WAL/foreign keys/FTS5 DB, RAG memory basics, tests.

## Out of scope
- Real Whisplay/PiSugar hardware access.
- Cloud provider credentials or live provider calls.
- Google Drive sync implementation beyond skeleton/docs if needed.
- Full product completion beyond Milestone 0/1.

## Acceptance criteria
- Failed team state is documented with concrete evidence.
- `/home/pi/vibe-dump` contains importable Python package.
- SQLite initializes schema with WAL, foreign keys, and FTS5.
- Dumps/turns/blueprints/chunks support insert/search/delete-cascade basics.
- Fake hardware/providers exist for later milestones.
- Tests pass for database/RAG basics and imports.
- README describes current scaffold and next steps.
