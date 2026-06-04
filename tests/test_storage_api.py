"""Tests for the M6 storage HTTP routes (rclone sync, export/import, redact).

These cover the five new endpoints:

* GET  /api/storage/status
* POST /api/storage/sync
* POST /api/storage/export
* POST /api/storage/import
* POST /api/storage/redact-preview

The ``FakeRcloneBridge`` from ``vibedump.integrations.rclone_sync`` is
used so the sync path is fully hermetic.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vibedump.agent_pipeline import AgentPipeline  # noqa: E402
from vibedump.app import AppState, create_app  # noqa: E402
from vibedump.database import Database  # noqa: E402
from vibedump.events import EventBus  # noqa: E402
from vibedump.integrations.backup import BACKUP_VERSION, export_bundle  # noqa: E402
from vibedump.integrations.rclone_sync import (  # noqa: E402
    FakeRcloneBridge,
    RcloneBridge,
)
from vibedump.providers import fake_registry  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_state(tmp_path: Path) -> AppState:
    """Build an AppState wired with a FakeRcloneBridge and a tmp data dir."""
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry = fake_registry()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=registry, bus=bus),
        memory=RagMemory(db),
        rclone=FakeRcloneBridge(),
        data_dir=tmp_path,
    )
    return state


@pytest.fixture
def client(app_state: AppState) -> TestClient:
    return TestClient(create_app(app_state))


@pytest.fixture
def unconfigured_state(tmp_path: Path) -> AppState:
    """AppState with no rclone bridge; used to verify the 503 path."""
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=fake_registry(), bus=bus),
        memory=RagMemory(db),
        rclone=None,
        data_dir=tmp_path,
    )
    return state


# ---------------------------------------------------------------------------
# /api/storage/status
# ---------------------------------------------------------------------------


def test_storage_status_returns_shape(client: TestClient) -> None:
    """Status returns the documented JSON envelope even with no activity yet."""
    res = client.get("/api/storage/status")
    assert res.status_code == 200
    body = res.json()
    for key in (
        "rclone_available",
        "remote",
        "host_layout",
        "last_export_at",
        "last_sync_at",
        "last_sync_result",
    ):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["rclone_available"], bool)
    assert isinstance(body["remote"], str) and body["remote"]
    # All three last_* values must default to null until activity happens.
    assert body["last_export_at"] is None
    assert body["last_sync_at"] is None
    assert body["last_sync_result"] is None
    # host_layout is a dict of name -> relative path.
    assert isinstance(body["host_layout"], dict)
    assert "dumps" in body["host_layout"]


def test_storage_status_rclone_available_uses_real_or_fake(
    client: TestClient,
) -> None:
    """A FakeRcloneBridge registers as available, so the flag is True."""
    res = client.get("/api/storage/status")
    body = res.json()
    assert body["rclone_available"] is True


# ---------------------------------------------------------------------------
# /api/storage/sync
# ---------------------------------------------------------------------------


def test_storage_sync_returns_ok(
    client: TestClient, app_state: AppState
) -> None:
    """Sync against the fake bridge returns ok=True and updates state."""
    res = client.post("/api/storage/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["error"] is None
    # last_sync_at + last_sync_result should now be populated.
    status = client.get("/api/storage/status").json()
    assert status["last_sync_at"] is not None
    assert status["last_sync_result"] is not None
    assert status["last_sync_result"]["ok"] is True
    # The bridge recorded exactly one sync call against our tmp dir.
    bridge = app_state.rclone
    assert isinstance(bridge, FakeRcloneBridge)
    assert len(bridge.sync_calls) == 1
    src, remote = bridge.sync_calls[0]
    assert src == str(app_state.data_dir)
    assert remote == "vibedump"


def test_storage_sync_503_when_unavailable(
    unconfigured_state: AppState,
) -> None:
    """Without a bridge, /sync must return 503 with a clear error message."""
    c = TestClient(create_app(unconfigured_state))
    res = c.post("/api/storage/sync")
    assert res.status_code == 503
    body = res.json()
    assert "rclone" in (body.get("detail") or "").lower()


# ---------------------------------------------------------------------------
# /api/storage/export
# ---------------------------------------------------------------------------


def test_storage_export_returns_path_and_manifest(client: TestClient) -> None:
    """Export creates a zip and returns its path + manifest + size_bytes."""
    res = client.post("/api/storage/export", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["path"].endswith(".zip")
    assert isinstance(body["size_bytes"], int) and body["size_bytes"] > 0
    manifest = body["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["version"] == BACKUP_VERSION
    # The export route also stamps last_export_at.
    status = client.get("/api/storage/status").json()
    assert status["last_export_at"] is not None


def test_storage_export_writes_zip(
    client: TestClient, app_state: AppState
) -> None:
    """The zip exists on disk and is a real, readable zip file."""
    res = client.post("/api/storage/export", json={})
    assert res.status_code == 200
    zip_path = Path(res.json()["path"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Real zips expose a name list and a manifest.json entry.
        names = zf.namelist()
        assert "manifest.json" in names
        manifest_blob = json.loads(zf.read("manifest.json"))
        assert manifest_blob["version"] == BACKUP_VERSION


def test_storage_export_dest_name_sanitised(
    client: TestClient, app_state: AppState
) -> None:
    """Custom ``dest_name`` lands in the filename after sanitisation."""
    res = client.post(
        "/api/storage/export", json={"dest_name": "My First Backup!"}
    )
    assert res.status_code == 200
    path = res.json()["path"]
    assert "My_First_Backup" in path
    # The file is still a real zip and still rooted under the data dir.
    assert Path(path).exists()


# ---------------------------------------------------------------------------
# /api/storage/import
# ---------------------------------------------------------------------------


def test_storage_import_round_trip(
    client: TestClient, app_state: AppState, tmp_path: Path
) -> None:
    """Export-then-import through the HTTP routes preserves the bundle."""
    # Seed a dump + provider config in the *same* DB used by the routes.
    dump_id = app_state.db.create_dump("hello world", {"k": "v"})
    app_state.db.add_turn(dump_id, "user", "hi there")
    app_state.db.add_blueprint(dump_id, "# Spec\n\nYes")
    app_state.db.upsert_provider_config(
        "openrouter", "llm", True, {"model": "gpt-4"}
    )

    # Export a fresh zip via the export route.
    export = client.post("/api/storage/export", json={})
    assert export.status_code == 200
    zip_bytes = Path(export.json()["path"]).read_bytes()

    # Wipe the DB so import is observable (use a fresh in-memory db).
    app_state.db.delete_dump(dump_id)
    assert app_state.db.get_dump(dump_id) is None

    files = {"file": ("vibedump.zip", io.BytesIO(zip_bytes), "application/zip")}
    res = client.post("/api/storage/import", files=files)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    manifest = body["manifest"]
    assert manifest["version"] == BACKUP_VERSION
    assert manifest["dump_count"] >= 1
    # The dump survived the round trip.
    assert app_state.db.get_dump(dump_id) is not None


def test_storage_import_rejects_non_zip(client: TestClient) -> None:
    """A non-zip upload is rejected with 415 (unsupported media type)."""
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    res = client.post("/api/storage/import", files=files)
    assert res.status_code == 415
    assert "zip" in (res.json().get("detail") or "").lower()


def test_storage_import_rejects_wrong_version(
    client: TestClient, app_state: AppState, tmp_path: Path
) -> None:
    """A zip whose manifest.version != BACKUP_VERSION is rejected with 422."""
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 99,
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
    files = {"file": ("bad.zip", bad_zip.read_bytes(), "application/zip")}
    res = client.post("/api/storage/import", files=files)
    assert res.status_code == 422
    detail = (res.json().get("detail") or "").lower()
    assert "version" in detail


# ---------------------------------------------------------------------------
# /api/storage/redact-preview
# ---------------------------------------------------------------------------


def test_storage_redact_preview_strips_sensitive_keys(client: TestClient) -> None:
    """The preview route scrubs every REDACT_KEYS leaf, recursively."""
    payload = {
        "config": {
            "api_key": "sk-very-secret-value-9876543210",
            "model": "gpt-4",
            "nested": {"token": "nope", "keep": "yes"},
        }
    }
    res = client.post("/api/storage/redact-preview", json=payload)
    assert res.status_code == 200
    redacted = res.json()["redacted"]
    # The sensitive leaves are gone, the non-sensitive ones are not.
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["model"] == "gpt-4"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["keep"] == "yes"
    # Original input is never mutated.
    assert payload["config"]["api_key"] == "sk-very-secret-value-9876543210"


# ---------------------------------------------------------------------------
# Persistence of storage_state
# ---------------------------------------------------------------------------


def test_storage_state_persists_last_export_at(
    client: TestClient,
) -> None:
    """After an export, the in-memory storage_state retains last_export_at."""
    res = client.post("/api/storage/export", json={})
    assert res.status_code == 200
    status = client.get("/api/storage/status").json()
    assert status["last_export_at"] is not None
    # Calling /status again must keep the timestamp unchanged.
    again = client.get("/api/storage/status").json()
    assert again["last_export_at"] == status["last_export_at"]


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_storage_sync_publishes_event(client: TestClient, app_state: AppState) -> None:
    """A successful sync publishes storage.sync_ok on the event bus."""
    client.post("/api/storage/sync")
    events = [e for e in app_state.bus.snapshot() if e.event_type == "storage.sync_ok"]
    assert events, "storage.sync_ok event was not published"
    payload = events[-1].payload
    assert payload["ok"] is True
    assert "synced_at" in payload


def test_storage_export_publishes_event(
    client: TestClient, app_state: AppState
) -> None:
    """Export publishes storage.export_ok with the manifest payload."""
    client.post("/api/storage/export", json={})
    events = [
        e for e in app_state.bus.snapshot() if e.event_type == "storage.export_ok"
    ]
    assert events, "storage.export_ok event was not published"
    payload = events[-1].payload
    assert "path" in payload
    assert "manifest" in payload
    assert isinstance(payload["size_bytes"], int)
