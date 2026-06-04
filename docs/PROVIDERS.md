# Providers

Only fake providers are implemented in Milestone 0/1. Real API adapters must use environment variables or local config and must never commit keys.

Planned provider families:

- OpenRouter
- Gemini / Google AI Studio
- NVIDIA NIM
- MiniMax
- OpenAI
- Groq
- Deepgram
- ElevenLabs

When Milestone 3 adds real adapters, document credential setup without committing actual values. Keep `.env.example` free of provider credential assignments so secret scanners stay quiet.
