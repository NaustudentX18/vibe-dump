"""Redacted config export for debug bundles and support snapshots.

Walks dicts and lists recursively and replaces values whose key matches
``REDACT_KEYS`` (case-insensitive) with ``REDACTED_PLACEHOLDER``. Inputs that
are neither a dict nor a list are returned unchanged. The ``redact`` helper
never mutates its input.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vibedump.database import Database

REDACTED_PLACEHOLDER = "[REDACTED]"

REDACT_KEYS = frozenset({
    "api_key",
    "secret",
    "token",
    "password",
    "openai_api_key",
    "anthropic_api_key",
    "gemini_api_key",
    "groq_api_key",
    "nvidia_api_key",
    "openrouter_api_key",
    "minimax_api_key",
    "elevenlabs_api_key",
})

REDACTED_CONFIG_DIRNAME = "provider-config-redacted"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ConfigRedactionError(ValueError):
    """Raised when a config payload cannot be parsed or redacted."""


def _sanitize_filename(piece: str) -> str:
    """Coerce ``piece`` to something safe to embed in a filename."""
    cleaned = _SAFE_NAME_RE.sub("_", piece).strip("._-")
    return cleaned or "x"


def _is_sensitive_key(key: Any) -> bool:
    """Case-insensitive membership check against ``REDACT_KEYS``."""
    if not isinstance(key, str):
        return False
    return key.lower() in REDACT_KEYS


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive leaves replaced.

    Dicts and lists are walked recursively. For any dict entry whose key
    (case-insensitive) is in ``REDACT_KEYS`` the value is replaced with
    ``REDACTED_PLACEHOLDER`` regardless of its type. Other values are
    recursed into or returned unchanged. The input is never mutated.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
                out[key] = REDACTED_PLACEHOLDER
            else:
                out[key] = redact(child)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def redact_config_json(raw_json: str) -> str:
    """Parse ``raw_json``, redact it, and re-serialize deterministically.

    Output is pretty-printed (``indent=2``) with sorted keys so re-exports
    produce stable diffs. ``json.JSONDecodeError`` is re-raised as
    ``ConfigRedactionError``.
    """
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigRedactionError(f"invalid JSON: {exc}") from exc
    return json.dumps(redact(parsed), indent=2, sort_keys=True)


def export_redacted_configs(db: Database, dest_dir: Path) -> Path:
    """Write one redacted JSON per ``provider_configs`` row plus a manifest.

    Files land in ``dest_dir / REDACTED_CONFIG_DIRNAME`` and follow the
    naming ``<id>_<kind>_<name>.json``. ``manifest.json`` records a UTC
    timestamp and the row count. Returns ``dest_dir`` (not the redacted
    subdirectory) so callers can keep chaining operations on the parent.
    """
    out_root = Path(dest_dir) / REDACTED_CONFIG_DIRNAME
    out_root.mkdir(parents=True, exist_ok=True)

    rows = db.list_provider_configs()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for row in rows:
        redacted_with_meta = {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "enabled": row.enabled,
            "config": redact(row.config),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        filename = (
            f"{row.id}_{_sanitize_filename(row.kind)}"
            f"_{_sanitize_filename(row.name)}.json"
        )
        (out_root / filename).write_text(
            json.dumps(redacted_with_meta, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    manifest = {
        "count": len(rows),
        "directory": REDACTED_CONFIG_DIRNAME,
        "generated_at": timestamp,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return Path(dest_dir)
