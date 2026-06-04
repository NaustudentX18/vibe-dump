"""Tests for the redacted config export helper."""

from __future__ import annotations

import json

import pytest

from vibedump.database import Database
from vibedump.integrations.config_redact import (
    ConfigRedactionError,
    REDACTED_CONFIG_DIRNAME,
    REDACTED_PLACEHOLDER,
    REDACT_KEYS,
    export_redacted_configs,
    redact,
    redact_config_json,
)


def test_redact_simple_dict():
    value = {"name": "openai", "api_key": "sk-fake-test-value"}
    out = redact(value)
    assert out == {"name": "openai", "api_key": REDACTED_PLACEHOLDER}


def test_redact_nested_dict():
    value = {
        "outer": {"inner": {"openai_api_key": "sk-fake-test-value"}},
        "kept": 1,
    }
    out = redact(value)
    assert out == {
        "outer": {"inner": {"openai_api_key": REDACTED_PLACEHOLDER}},
        "kept": 1,
    }


def test_redact_list_of_dicts():
    value = {
        "items": [
            {"api_key": "sk-fake-test-value"},
            {"token": "fake-token-value"},
        ]
    }
    out = redact(value)
    assert out == {
        "items": [
            {"api_key": REDACTED_PLACEHOLDER},
            {"token": REDACTED_PLACEHOLDER},
        ]
    }


def test_redact_case_insensitive_key_match():
    value = {
        "API_KEY": "sk-fake-test-value",
        "Secret": "x",
        "PaSsWoRd": "y",
        "ElevenLabs_Api_Key": "z",
    }
    out = redact(value)
    assert out == {
        "API_KEY": REDACTED_PLACEHOLDER,
        "Secret": REDACTED_PLACEHOLDER,
        "PaSsWoRd": REDACTED_PLACEHOLDER,
        "ElevenLabs_Api_Key": REDACTED_PLACEHOLDER,
    }


def test_redact_preserves_non_string_leaves():
    value = {"count": 5, "enabled": True, "ratio": 0.25, "empty": None}
    out = redact(value)
    assert out == value


def test_redact_passthrough_for_non_collection():
    assert redact("hello") == "hello"
    assert redact(42) == 42
    assert redact(True) is True
    assert redact(None) is None


def test_redact_does_not_mutate_input():
    value = {
        "api_key": "sk-fake-test-value",
        "nested": {"token": "fake-token-value", "kept": "ok"},
    }
    snapshot = json.dumps(value, sort_keys=True)
    _ = redact(value)
    assert json.dumps(value, sort_keys=True) == snapshot


def test_redact_config_json_round_trip():
    raw = json.dumps(
        {"api_key": "sk-fake-test-value", "model": "gpt-4", "extra": [1, 2, 3]}
    )
    out = redact_config_json(raw)
    parsed = json.loads(out)
    assert parsed == {
        "api_key": REDACTED_PLACEHOLDER,
        "model": "gpt-4",
        "extra": [1, 2, 3],
    }
    # Pretty-printed, not single-line.
    assert "\n  " in out


def test_redact_config_json_invalid_raises():
    with pytest.raises(ConfigRedactionError):
        redact_config_json("{not valid json}")


def test_export_redacted_configs_writes_files(tmp_path):
    db = Database(tmp_path / "vd.sqlite3")
    db.initialize()
    db.upsert_provider_config(
        "openai",
        "llm",
        True,
        {"api_key": "sk-fake-test-value", "model": "gpt-4"},
    )

    result = export_redacted_configs(db, tmp_path / "out")
    assert result == tmp_path / "out"

    out_dir = tmp_path / "out" / REDACTED_CONFIG_DIRNAME
    files = [p for p in out_dir.glob("*.json") if p.name != "manifest.json"]
    assert len(files) == 1

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["name"] == "openai"
    assert payload["kind"] == "llm"
    assert payload["config"] == {
        "api_key": REDACTED_PLACEHOLDER,
        "model": "gpt-4",
    }
    assert files[0].name.endswith("_llm_openai.json")
    db.close()


def test_export_redacted_configs_writes_manifest(tmp_path):
    db = Database(tmp_path / "vd.sqlite3")
    db.initialize()
    db.upsert_provider_config("openai", "llm", True, {"api_key": "sk-fake-test-value"})
    db.upsert_provider_config("elevenlabs", "tts", False, {"api_key": "x-fake"})

    export_redacted_configs(db, tmp_path / "out")
    manifest = json.loads(
        (tmp_path / "out" / REDACTED_CONFIG_DIRNAME / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["count"] == 2
    assert "generated_at" in manifest and manifest["generated_at"]
    assert manifest["directory"] == REDACTED_CONFIG_DIRNAME
    db.close()


def test_export_redacted_configs_strips_secret_values(tmp_path):
    db = Database(tmp_path / "vd.sqlite3")
    db.initialize()
    fake_value = "sk-fake-test-value"
    db.upsert_provider_config(
        "openai",
        "llm",
        True,
        {
            "api_key": fake_value,
            "openai_api_key": fake_value,
            "model": "gpt-4",
        },
    )

    export_redacted_configs(db, tmp_path / "out")
    out_dir = tmp_path / "out" / REDACTED_CONFIG_DIRNAME
    combined = " ".join(p.read_text(encoding="utf-8") for p in out_dir.glob("*.json"))
    assert fake_value not in combined
    assert REDACTED_PLACEHOLDER in combined
    # Redact key set is still the source of truth.
    assert "api_key" in REDACT_KEYS
    db.close()
