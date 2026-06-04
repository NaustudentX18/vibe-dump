"""Zip-based backup and restore for Vibe-Dump data.

Exports a SQLite database plus its audio files into a single zip bundle that
can be re-imported on a fresh installation. Provider configuration is written
in redacted form to keep credentials out of the bundle.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from vibedump import __version__ as APP_VERSION

if TYPE_CHECKING:
    from vibedump.database import Database


BACKUP_VERSION = "1"
BUNDLE_README = "This is a Vibe-Dump backup bundle. Do not edit files inside."

_MANIFEST_NAME = "manifest.json"
_DUMPS_NAME = "dumps.jsonl"
_TURNS_NAME = "turns.jsonl"
_BLUEPRINTS_NAME = "blueprints.jsonl"
_PROVIDER_DIR = "provider-config-redacted"
_AUDIO_DIR = "audio"
_README_NAME = "README-DO-NOT-DELETE.md"
_DUMP_PAGE = 500
_REDACTED_VALUE = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Top-level metadata for a backup bundle."""

    version: str
    created_at: str
    dump_count: int
    blueprint_count: int
    turn_count: int
    audio_count: int
    config_count: int
    app_version: str


class ExportError(ValueError):
    """Raised when a backup export cannot be completed."""


class ImportError_(ValueError):
    """Raised when a backup import cannot be completed."""


# ---------------------------------------------------------------------------
# Redaction (delegates to M6-P2's config_redact; falls back to in-module walk)
# ---------------------------------------------------------------------------
try:
    from vibedump.integrations.config_redact import (  # type: ignore[import-not-found]
        redact_config_json as _redact_config_json,
    )
except ImportError:  # pragma: no cover - exercised only when M6-P2 is absent

    def _redact_config_json(raw_json: str) -> str:
        """In-module redaction fallback; mirrors the M6-P2 string-in/string-out contract."""
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return raw_json
        return json.dumps(_redact(parsed), indent=2, sort_keys=True)

    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (_REDACTED_VALUE if _is_sensitive(key) else _redact(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    def _is_sensitive(field_name: str) -> bool:
        lowered = field_name.lower()
        return any(
            marker in lowered
            for marker in ("api_key", "secret", "token", "password")
        )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def build_manifest(db: Database, audio_dir: Path) -> BackupManifest:
    """Build a manifest describing what ``db`` and ``audio_dir`` contain."""
    dump_count = db.count("dumps")
    turn_count = db.count("turns")
    blueprint_count = db.count("blueprints")
    config_count = len(db.list_provider_configs())
    audio_count = _count_exportable_audio(db, audio_dir)
    return BackupManifest(
        version=BACKUP_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        dump_count=dump_count,
        blueprint_count=blueprint_count,
        turn_count=turn_count,
        audio_count=audio_count,
        config_count=config_count,
        app_version=APP_VERSION,
    )


def _count_exportable_audio(db: Database, audio_dir: Path) -> int:
    """Count turns whose audio file lives under ``audio_dir`` and is readable."""
    if not audio_dir.is_dir():
        return 0
    base = audio_dir.resolve()
    total = 0
    for dump in _iter_all_dumps(db):
        for turn in db.list_turns(dump.id):
            if _audio_path_is_under(turn.audio_path, base):
                total += 1
    return total


def _audio_path_is_under(audio_path: str | None, base: Path) -> bool:
    if not audio_path:
        return False
    try:
        candidate = Path(audio_path).resolve()
    except OSError:
        return False
    if not candidate.is_file():
        return False
    return candidate.is_relative_to(base)


# ---------------------------------------------------------------------------
# Iteration helpers
# ---------------------------------------------------------------------------
def _iter_all_dumps(db: Database) -> Iterable[Any]:
    offset = 0
    while True:
        page = db.list_dumps(limit=_DUMP_PAGE, offset=offset)
        if not page:
            return
        yield from page
        offset += _DUMP_PAGE


def _iter_all_turns(db: Database) -> Iterable[Any]:
    for dump in _iter_all_dumps(db):
        yield from db.list_turns(dump.id)


def _iter_all_blueprints(db: Database) -> Iterable[Any]:
    for dump in _iter_all_dumps(db):
        blueprint = db.get_latest_blueprint(dump.id)
        if blueprint is not None:
            yield blueprint


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_bundle(
    db: Database,
    audio_dir: Path,
    dest_zip: Path,
) -> BackupManifest:
    """Export ``db`` and ``audio_dir`` to a new zip at ``dest_zip``."""
    manifest = build_manifest(db, audio_dir)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST_NAME, json.dumps(asdict(manifest), indent=2, sort_keys=True))
        zf.writestr(_DUMPS_NAME, _dump_to_jsonl(_iter_all_dumps(db)))
        zf.writestr(_TURNS_NAME, _dump_to_jsonl(_iter_all_turns(db)))
        zf.writestr(_BLUEPRINTS_NAME, _dump_to_jsonl(_iter_all_blueprints(db)))
        _write_provider_configs(zf, db)
        _write_audio_files(zf, db, audio_dir)
        zf.writestr(_README_NAME, BUNDLE_README)

    return manifest


def _dump_to_jsonl(rows: Iterable[Any]) -> str:
    lines = [json.dumps(asdict(row), sort_keys=True) for row in rows]
    return "\n".join(lines)


def _write_provider_configs(zf: zipfile.ZipFile, db: Database) -> None:
    for config in db.list_provider_configs():
        raw_json = json.dumps(asdict(config), sort_keys=True)
        redacted_json = _redact_config_json(raw_json)
        arcname = f"{_PROVIDER_DIR}/{config.id}.json"
        zf.writestr(arcname, redacted_json)


def _write_audio_files(zf: zipfile.ZipFile, db: Database, audio_dir: Path) -> None:
    if not audio_dir.is_dir():
        return
    base = audio_dir.resolve()
    seen: set[tuple[int, int]] = set()
    for turn in _iter_all_turns(db):
        if not _audio_path_is_under(turn.audio_path, base):
            continue
        key = (turn.dump_id, turn.id)
        if key in seen:
            continue
        seen.add(key)
        src = Path(turn.audio_path).resolve()
        zf.write(src, f"{_AUDIO_DIR}/{turn.dump_id}/{turn.id}.wav")


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def import_bundle(
    db: Database,
    audio_dir: Path,
    src_zip: Path,
) -> BackupManifest:
    """Restore ``src_zip`` into ``db`` and ``audio_dir``. Idempotent."""
    if not src_zip.is_file():
        raise ImportError_(f"backup not found: {src_zip}")
    with zipfile.ZipFile(src_zip, "r") as zf:
        manifest = _load_manifest(zf)
        if manifest.version != BACKUP_VERSION:
            raise ImportError_(
                f"unsupported backup version: {manifest.version!r} (expected {BACKUP_VERSION!r})"
            )
        _import_dumps(zf, db)
        _import_provider_configs(zf, db)
        _import_turns(zf, db, audio_dir)
        _import_blueprints(zf, db)
    return manifest


def _load_manifest(zf: zipfile.ZipFile) -> BackupManifest:
    try:
        payload = zf.read(_MANIFEST_NAME)
    except KeyError as exc:
        raise ImportError_(f"missing {_MANIFEST_NAME} in bundle") from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImportError_(f"manifest is not valid JSON: {exc}") from exc
    try:
        return BackupManifest(**data)
    except TypeError as exc:
        raise ImportError_(f"manifest has unexpected shape: {exc}") from exc


def _read_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    if name not in zf.namelist():
        return []
    raw = zf.read(name).decode("utf-8")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _import_dumps(zf: zipfile.ZipFile, db: Database) -> None:
    for row in _read_jsonl(zf, _DUMPS_NAME):
        metadata = json.dumps(row.get("metadata") or {}, sort_keys=True)
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO dumps(
                    id, title, status, created_at, updated_at, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["title"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    metadata,
                ),
            )


def _import_blueprints(zf: zipfile.ZipFile, db: Database) -> None:
    for row in _read_jsonl(zf, _BLUEPRINTS_NAME):
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO blueprints(id, dump_id, markdown, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["dump_id"],
                    row["markdown"],
                    row["created_at"],
                ),
            )


def _import_provider_configs(zf: zipfile.ZipFile, db: Database) -> None:
    for name in zf.namelist():
        if not name.startswith(f"{_PROVIDER_DIR}/") or not name.endswith(".json"):
            continue
        try:
            payload = json.loads(zf.read(name))
        except json.JSONDecodeError:
            continue
        config = payload.get("config") or {}
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO provider_configs(
                    id, name, kind, enabled, config_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["name"],
                    payload["kind"],
                    1 if payload.get("enabled") else 0,
                    json.dumps(config, sort_keys=True),
                    payload.get("created_at"),
                    payload.get("updated_at"),
                ),
            )


def _import_turns(zf: zipfile.ZipFile, db: Database, audio_dir: Path) -> None:
    rows = _read_jsonl(zf, _TURNS_NAME)
    if not rows:
        return
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_base = audio_dir.resolve()
    for row in rows:
        old_audio = row.get("audio_path")
        new_audio = _restore_audio(zf, row, audio_dir, audio_base)
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO turns(
                    id, dump_id, role, text, audio_path, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["dump_id"],
                    row["role"],
                    row["text"],
                    new_audio or old_audio,
                    row["created_at"],
                ),
            )


def _restore_audio(
    zf: zipfile.ZipFile,
    row: dict[str, Any],
    audio_dir: Path,
    audio_base: Path,
) -> str | None:
    arcname = f"{_AUDIO_DIR}/{row['dump_id']}/{row['id']}.wav"
    if arcname not in zf.namelist():
        return None
    target = audio_dir / str(row["dump_id"]) / f"{row['id']}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(arcname) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return _relative_to_base(target, audio_base)


def _relative_to_base(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


__all__ = [
    "BACKUP_VERSION",
    "BUNDLE_README",
    "BackupManifest",
    "ExportError",
    "ImportError_",
    "build_manifest",
    "export_bundle",
    "import_bundle",
]
