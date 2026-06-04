"""LLM provider selection for the M4 active-listener pipeline.

Precedence, highest first:
  1. ``VIBEDUMP_LLM_PROVIDER`` env var (matches registry by name). If the
     name is not registered, the selection fails (returns ``None``) so
     the caller can surface the misconfiguration.
  2. First enabled ``provider_configs`` row where ``kind="llm"``, ordered
     by ``(updated_at DESC, id DESC)`` — i.e. the most recently upserted
     row wins; on a tie, the higher id (last inserted) wins. If that
     name is not in the registry, fall through to (3).
  3. First LLM in ``registry.llm`` (sorted by key for determinism).

Returns ``None`` when no provider can be resolved.
"""

from __future__ import annotations

import os

from .database import Database
from .providers.base import LLMProvider, ProviderRegistry

_ENV_VAR = "VIBEDUMP_LLM_PROVIDER"


def select_llm_provider(
    registry: ProviderRegistry,
    db: Database | None = None,
) -> LLMProvider | None:
    env_name = os.environ.get(_ENV_VAR, "").strip()
    if env_name:
        return registry.llm.get(env_name)

    if db is not None:
        # Database.list_provider_configs orders by (kind, name); we need the
        # most-recently-updated enabled LLM. Sort here so a re-upsert of an
        # older row still wins, which matches the env-var-is-sticky-otherwise
        # "last write wins" expectation.
        candidates = sorted(
            (
                row for row in db.list_provider_configs()
                if row.kind == "llm" and row.enabled
            ),
            key=lambda r: (r.updated_at, r.id),
            reverse=True,
        )
        for row in candidates:
            provider = registry.llm.get(row.name)
            if provider is not None:
                return provider

    if not registry.llm:
        return None
    first_key = sorted(registry.llm.keys())[0]
    return registry.llm[first_key]


__all__ = ["select_llm_provider"]
