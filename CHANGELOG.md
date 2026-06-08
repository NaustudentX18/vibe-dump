# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Refreshed README to make the voice-dump → blueprint loop the headline.
- Tightened CI: ruff, pytest with coverage gate, and a build smoke test now run
  on every PR.
- Replaced the legacy placeholder license with the project MIT license and
  added the standard community files (`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`).

## [0.2.0] - 2026-06-06

### Added
- **Voice-dump → blueprint loop:** end-to-end pipeline that turns a spoken
  brain-dump into a structured project blueprint, ready to be handed to a
  build agent.
- **pydantic-graph swarm:** a graph-based orchestration layer for running
  multiple LLM-driven sub-agents with typed state and explicit edges.
- **7 LLM providers:** OpenAI, Anthropic, Google, Mistral, Ollama, OpenRouter,
  and a generic OpenAI-compatible endpoint, all behind a single adapter
  interface.
- **Mobile dashboard:** a FastAPI + responsive web frontend for reviewing
  blueprints, transcripts, and run history from a phone.
- **Dumpi mascot:** the small dumpling who lives in the docs, the dashboard,
  and the CLI splash.
- **Hardware bridges:** Whisplay HAT (display + buttons) and PiSugar
  battery/charging telemetry are now first-class integrations on the
  Raspberry Pi 5 reference platform.
- **SQLite + FTS5 storage:** local-first persistence with full-text search
  over transcripts and blueprints.
- **rclone sync:** optional push of the local store to any rclone remote
  (S3, Drive, SFTP, etc.) for off-device backup.
- **517 tests** in the suite, covering the voice pipeline, swarm graph,
  provider adapters, storage layer, and hardware bridges.

### Notes
- This is the first release tagged for the public repo. Pre-0.2.0 history
  lives in the commit log but was not formally versioned.

[Unreleased]: https://github.com/NaustudentX18/vibe-dump/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/NaustudentX18/vibe-dump/releases/tag/v0.2.0
