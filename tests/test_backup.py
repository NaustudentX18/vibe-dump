"""Tests for vibedump.integrations.backup."""

from __future__ import annotations

import json
import wave
import zipfile
from pathlib import Path

import pytest

from vibedump.database import Database
from vibedump.integrations.backup import (
    BACKUP_VERSION,
    BUNDLE_README,
    BackupManifest,
    ExportError,
    ImportError_,
    build_manifest,
    export_bundle,
    import_bundle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_basic_dump(db: Database) -> tuple[int, int, int]:
    """Create one dump with one turn and one blueprint. Return their ids."""
    dump_id = db.create_dump("Pocket Manus")
    turn_id = db.add_turn(dump_id, "user", "Build a tiny voice spec")
    blueprint_id = db.add_blueprint(dump_id, "# Vibe Blueprint\nBuild it.\n")
    return dump_id, turn_id, blueprint_id


def _write_silent_wav(path: Path, *, duration_s: float = 0.1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    num_frames = max(1, int(duration_s * sample_rate))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames)


def _init_db(tmp_path: Path, name: str = "vibedump.sqlite3") -> Database:
    db = Database(tmp_path / name)
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def test_manifest_version_constant() -> None:
    assert BACKUP_VERSION == "1"
    assert BUNDLE_README.startswith("This is a Vibe-Dump backup bundle")


def test_build_manifest_with_empty_db(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    manifest = build_manifest(db, audio_dir)

    assert isinstance(manifest, BackupManifest)
    assert manifest.version == BACKUP_VERSION
    assert manifest.dump_count == 0
    assert manifest.turn_count == 0
    assert manifest.blueprint_count == 0
    assert manifest.audio_count == 0
    assert manifest.config_count == 0
    assert manifest.app_version
    # ISO 8601 UTC
    assert manifest.created_at.endswith("+00:00") or manifest.created_at.endswith("Z")
    db.close()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def test_export_bundle_creates_zip(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    dest = tmp_path / "out" / "backup.zip"
    manifest = export_bundle(db, tmp_path / "audio", dest)

    assert dest.is_file()
    assert manifest.version == BACKUP_VERSION
    with zipfile.ZipFile(dest) as zf:
        assert "manifest.json" in zf.namelist()
    db.close()


def test_export_bundle_includes_manifest(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    _seed_basic_dump(db)
    dest = tmp_path / "backup.zip"

    export_bundle(db, tmp_path / "audio", dest)

    with zipfile.ZipFile(dest) as zf:
        data = json.loads(zf.read("manifest.json"))
    assert data["version"] == BACKUP_VERSION
    assert data["dump_count"] == 1
    assert data["turn_count"] == 1
    assert data["blueprint_count"] == 1
    assert data["config_count"] == 0
    assert data["audio_count"] == 0
    db.close()


def test_export_bundle_includes_dumps_jsonl(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    _seed_basic_dump(db)
    dest = tmp_path / "backup.zip"

    export_bundle(db, tmp_path / "audio", dest)

    with zipfile.ZipFile(dest) as zf:
        assert "dumps.jsonl" in zf.namelist()
        assert "turns.jsonl" in zf.namelist()
        assert "blueprints.jsonl" in zf.namelist()
        dump_rows = [
            json.loads(line)
            for line in zf.read("dumps.jsonl").decode("utf-8").splitlines()
            if line.strip()
        ]
    assert len(dump_rows) == 1
    assert dump_rows[0]["title"] == "Pocket Manus"
    db.close()


def test_export_bundle_includes_readme(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    dest = tmp_path / "backup.zip"

    export_bundle(db, tmp_path / "audio", dest)

    with zipfile.ZipFile(dest) as zf:
        assert "README-DO-NOT-DELETE.md" in zf.namelist()
        assert zf.read("README-DO-NOT-DELETE.md").decode("utf-8") == BUNDLE_README
    db.close()


def test_export_bundle_includes_audio_when_present(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    dump_id, _, _ = _seed_basic_dump(db)
    audio_path = audio_dir / "turn-with-audio.wav"
    _write_silent_wav(audio_path)
    turn_id = db.add_turn(dump_id, "assistant", "ack", audio_path=str(audio_path))

    manifest = export_bundle(db, audio_dir, tmp_path / "backup.zip")

    assert manifest.audio_count == 1
    with zipfile.ZipFile(tmp_path / "backup.zip") as zf:
        assert f"audio/{dump_id}/{turn_id}.wav" in zf.namelist()
    db.close()


def test_export_bundle_redacts_provider_configs(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    _seed_basic_dump(db)
    secret_value = "sk-very-secret-value-9876543210"
    db.upsert_provider_config(
        "openrouter",
        "llm",
        True,
        {"api_key": secret_value, "model": "gpt-4", "nested": {"token": "nope"}},
    )

    dest = tmp_path / "backup.zip"
    export_bundle(db, tmp_path / "audio", dest)

    with zipfile.ZipFile(dest) as zf:
        names = [n for n in zf.namelist() if n.startswith("provider-config-redacted/")]
        assert names, "no provider config was written"
        for name in names:
            contents = zf.read(name).decode("utf-8")
            assert secret_value not in contents
            assert "sk-very-secret-value-9876543210" not in contents
            # the value must have been redacted
            assert "[REDACTED]" in contents
    db.close()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def test_import_bundle_round_trip(tmp_path: Path) -> None:
    src_db = _init_db(tmp_path, "src.sqlite3")
    _seed_basic_dump(src_db)
    src_db.upsert_provider_config("openrouter", "llm", True, {"model": "gpt-4"})
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    zip_path = tmp_path / "backup.zip"
    export_bundle(src_db, audio_dir, zip_path)
    src_db.close()

    fresh = _init_db(tmp_path, "fresh.sqlite3")
    manifest = import_bundle(fresh, tmp_path / "audio-fresh", zip_path)

    assert manifest.dump_count == 1
    assert fresh.count("dumps") == 1
    assert fresh.count("turns") == 1
    assert fresh.count("blueprints") == 1
    assert len(fresh.list_provider_configs()) == 1
    fresh.close()


def test_import_bundle_rejects_unknown_version(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": "99",
                    "created_at": "2026-06-05T00:00:00+00:00",
                    "dump_count": 0,
                    "blueprint_count": 0,
                    "turn_count": 0,
                    "audio_count": 0,
                    "config_count": 0,
                    "app_version": "0.1.0",
                }
            ),
        )

    with pytest.raises(ImportError_):
        import_bundle(db, tmp_path / "audio", zip_path)
    db.close()


def test_import_bundle_is_idempotent(tmp_path: Path) -> None:
    src_db = _init_db(tmp_path, "src.sqlite3")
    _seed_basic_dump(src_db)
    zip_path = tmp_path / "backup.zip"
    export_bundle(src_db, tmp_path / "audio", zip_path)
    src_db.close()

    fresh = _init_db(tmp_path, "fresh.sqlite3")
    import_bundle(fresh, tmp_path / "audio-fresh", zip_path)
    counts_after_first = {
        "dumps": fresh.count("dumps"),
        "turns": fresh.count("turns"),
        "blueprints": fresh.count("blueprints"),
    }
    import_bundle(fresh, tmp_path / "audio-fresh", zip_path)
    assert fresh.count("dumps") == counts_after_first["dumps"]
    assert fresh.count("turns") == counts_after_first["turns"]
    assert fresh.count("blueprints") == counts_after_first["blueprints"]
    fresh.close()


def test_import_bundle_writes_audio_files(tmp_path: Path) -> None:
    src_db = _init_db(tmp_path, "src.sqlite3")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    dump_id, _, _ = _seed_basic_dump(src_db)
    audio_path = audio_dir / "turn-with-audio.wav"
    _write_silent_wav(audio_path)
    turn_id = src_db.add_turn(dump_id, "assistant", "ack", audio_path=str(audio_path))
    zip_path = tmp_path / "backup.zip"
    export_bundle(src_db, audio_dir, zip_path)
    src_db.close()

    fresh_audio = tmp_path / "audio-fresh"
    fresh = _init_db(tmp_path, "fresh.sqlite3")
    import_bundle(fresh, fresh_audio, zip_path)

    extracted = fresh_audio / str(dump_id) / f"{turn_id}.wav"
    assert extracted.is_file()
    # the file is a real WAV (header sniff)
    with wave.open(str(extracted), "rb") as wf:
        assert wf.getframerate() == 16000
    # the turn's audio_path should now point inside the new audio dir
    turns = fresh.list_turns(dump_id)
    matching = [t for t in turns if t.id == turn_id]
    assert matching, "imported turn is missing"
    assert matching[0].audio_path is not None
    assert (fresh_audio / matching[0].audio_path).is_file()
    fresh.close()


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------
def test_export_and_import_errors_are_value_errors() -> None:
    assert issubclass(ExportError, ValueError)
    assert issubclass(ImportError_, ValueError)
