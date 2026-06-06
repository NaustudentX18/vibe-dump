# Vibe-Dump v0.2.0

**2026-06-05** — Second swarm pass: hardware truth, dashboard overhaul, M10 scaffolding.

## Highlights

- Whisplay WM8960 audio path (capture + `aplay` TTS after `[ASK]`)
- Dashboard split into static assets; markdown blueprints, PTT UX, tab bar, onboarding
- M10: memory store, swarm DAG, skills/evolution scaffolding, swarm SSE panel
- P2 UI: light theme, chat bubbles, dump filters, delete sheet, achievement overlay
- 517 automated tests + GitHub Actions CI

## Deferred to Pi / future

- Physical hardware gates (WM8960 verify, assembly photos 3–4)
- Real bge embeddings (M10-04), Smart Dumpi agent (M10-09), wakeword (M10-10)
- Pipecat/WebRTC (M10-12), 10-minute soak on device (QA-04)

## Upgrade

```bash
git pull origin master
pip install -e ".[web,all]" pillow
python -m pytest -q
```
