"""App factory placeholder.

FastAPI is optional in Milestone 0/1 so tests do not require web dependencies.
"""

from __future__ import annotations


def create_app():
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("Install vibe-dump[web] to use the web app") from exc

    app = FastAPI(title="Vibe-Dump")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return "<h1>💩 Vibe-Dump</h1>"

    return app
