"""Tests for the LLM provider selection function (M4).

Precedence, highest first:
  1. VIBEDUMP_LLM_PROVIDER env var (matches registry by name)
  2. First enabled provider_configs row where kind="llm", ordered by
     (updated_at DESC, id DESC) — most recently upserted wins; on a
     tie, the higher id (last inserted) wins.
  3. First LLM in the registry (sorted by name)

Returns None when no provider can be resolved.
"""

from __future__ import annotations

import pytest

from vibedump.database import Database
from vibedump.providers import fake_registry
from vibedump.providers.base import LLMProvider, ProviderHealth, ProviderRegistry
from vibedump.providers.llm import FakeLLM
from vibedump.provider_selection import select_llm_provider


def _named(name: str) -> LLMProvider:
    """Build a tiny stub LLMProvider carrying only a name."""

    class _Stub:
        def complete(self, prompt: str) -> str:
            return ""

        def health(self) -> ProviderHealth:
            return ProviderHealth(name, True, "stub")

    stub = _Stub()
    stub.name = name  # type: ignore[attr-defined]
    return stub  # type: ignore[return-value]


def _registry(*names: str) -> ProviderRegistry:
    registry = ProviderRegistry()
    for name in names:
        registry.llm[name] = _named(name)
    return registry


# ---------------------------------------------------------------------------
# 1. Env var set + name exists in registry -> that provider
# ---------------------------------------------------------------------------


def test_env_var_set_and_name_in_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBEDUMP_LLM_PROVIDER", "openrouter")
    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry)
    assert result is not None
    assert result.name == "openrouter"


# ---------------------------------------------------------------------------
# 2. Env var set + name NOT in registry -> None
# ---------------------------------------------------------------------------


def test_env_var_set_but_name_missing_from_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBEDUMP_LLM_PROVIDER", "ghost")
    registry = _registry("openrouter", "groq")
    assert select_llm_provider(registry) is None


# ---------------------------------------------------------------------------
# 3. Env var unset + provider_configs has enabled kind=llm row whose name
#    exists in registry -> that provider
# ---------------------------------------------------------------------------


def test_db_enabled_llm_row_picks_that_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()
    db.upsert_provider_config("groq", "llm", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "groq"
    db.close()


# ---------------------------------------------------------------------------
# 4. Env var unset + multiple enabled kind=llm rows -> first by
#    (updated_at DESC, id DESC)
# ---------------------------------------------------------------------------


def test_db_multiple_enabled_llm_rows_returns_most_recently_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()
    # Insert in order so "groq" has a later updated_at than "openrouter".
    db.upsert_provider_config("openrouter", "llm", True, {})
    db.upsert_provider_config("groq", "llm", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "groq"
    db.close()


# ---------------------------------------------------------------------------
# 5. Env var unset + only kind="stt" or kind="tts" rows -> fall through
#    to registry
# ---------------------------------------------------------------------------


def test_db_with_only_stt_and_tts_rows_falls_through_to_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()
    db.upsert_provider_config("whisper", "stt", True, {})
    db.upsert_provider_config("piper", "tts", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    # Registry has multiple LLMs; sorted by key => "groq" first.
    assert result is not None
    assert result.name == "groq"
    db.close()


# ---------------------------------------------------------------------------
# 6. Env var unset + db is None -> fall through to registry
# ---------------------------------------------------------------------------


def test_db_none_falls_through_to_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, None)
    assert result is not None
    assert result.name == "groq"


# ---------------------------------------------------------------------------
# 7. Env var unset + db row exists but its name is NOT in registry ->
#    fall through to registry
# ---------------------------------------------------------------------------


def test_db_row_name_not_in_registry_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()
    db.upsert_provider_config("ghost", "llm", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "groq"
    db.close()


# ---------------------------------------------------------------------------
# 8. Env var unset + db empty + registry has multiple LLMs -> first by
#    sorted key
# ---------------------------------------------------------------------------


def test_registry_first_by_sorted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()

    registry = _registry("zeta", "alpha", "mu")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "alpha"
    db.close()


# ---------------------------------------------------------------------------
# 9. Env var unset + db empty + registry empty -> None
# ---------------------------------------------------------------------------


def test_registry_empty_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()

    registry = ProviderRegistry()
    assert select_llm_provider(registry, db) is None
    db.close()


# ---------------------------------------------------------------------------
# 4b. Re-upsert of an existing row bumps updated_at and that row wins.
#     This pins the (updated_at DESC, id ASC) ordering — without it, a
#     re-upserted row that sorts earlier alphabetically would still win,
#     which is wrong.
# ---------------------------------------------------------------------------


def test_re_upserted_row_is_most_recently_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    db = Database(":memory:")
    db.initialize()
    # Insert "alpha" first, then "beta". Re-upserting "alpha" will update its
    # row but `updated_at` is second-precision in SQLite; we set it to a
    # known later value to avoid relying on wall-clock timing.
    db.upsert_provider_config("alpha", "llm", True, {})
    db.upsert_provider_config("beta", "llm", True, {})
    db.upsert_provider_config("alpha", "llm", True, {})
    db._conn.execute(
        "UPDATE provider_configs SET updated_at = ? WHERE name = ?",
        ("2099-01-01T00:00:00+00:00", "alpha"),
    )
    db._conn.commit()

    registry = _registry("alpha", "beta")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "alpha"
    db.close()


# ---------------------------------------------------------------------------
# 10. Env var is whitespace -> treated as unset
# ---------------------------------------------------------------------------


def test_env_var_whitespace_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBEDUMP_LLM_PROVIDER", "   ")
    db = Database(":memory:")
    db.initialize()
    db.upsert_provider_config("groq", "llm", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "groq"
    db.close()


# ---------------------------------------------------------------------------
# Bonus coverage: ProviderRegistry is accepted directly (the real shape
# callers will use), and the function also returns None when the env var
# points to a real registry key but the row fallback would resolve too --
# env var always wins.
# ---------------------------------------------------------------------------


def test_env_var_beats_db_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBEDUMP_LLM_PROVIDER", "openrouter")
    db = Database(":memory:")
    db.initialize()
    # DB row would normally win (later updated_at), but env var takes priority.
    db.upsert_provider_config("openrouter", "llm", True, {})
    db.upsert_provider_config("groq", "llm", True, {})

    registry = _registry("openrouter", "groq")
    result = select_llm_provider(registry, db)
    assert result is not None
    assert result.name == "openrouter"
    db.close()


def test_accepts_real_provider_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public registry factory must also satisfy the typing."""
    monkeypatch.delenv("VIBEDUMP_LLM_PROVIDER", raising=False)
    registry = fake_registry()
    result = select_llm_provider(registry)
    assert isinstance(result, FakeLLM)
