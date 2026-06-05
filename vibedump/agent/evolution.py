"""EvolutionHook: extract and persist lessons from pipeline runs.

The hook is called after each :class:`~vibedump.agent_pipeline.AgentPipeline`
run.  It uses a simple heuristic to extract bullet-point lessons from the
final blueprint or assistant turns, then persists them via
:class:`~vibedump.memory.lessons.LessonStore`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..memory.lessons import LessonStore


_BULLET_RE = re.compile(r"^[\s\-\*\u2022]+(.+)$", re.MULTILINE)


class EvolutionHook:
    """Post-run hook that distils lessons from a pipeline result.

    Parameters
    ----------
    lesson_store:
        Optional :class:`~vibedump.memory.lessons.LessonStore`.  When
        ``None`` the hook runs in dry-run mode: lessons are extracted but
        not persisted.
    """

    def __init__(self, lesson_store: "LessonStore | None" = None) -> None:
        self._store = lesson_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_lessons(self, text: str, tags: list[str] | None = None) -> list[str]:
        """Extract bullet-point sentences from ``text`` as candidate lessons.

        Each bullet item (``-``, ``*``, ``•``) that is at least 10 characters
        long is treated as a lesson.  The caller can pass additional ``tags``
        that are forwarded to the :class:`~vibedump.memory.lessons.LessonStore`
        when persisting.

        Returns the list of extracted lesson strings whether or not a store
        is configured (callers can inspect the output in tests without a DB).
        """
        lessons: list[str] = []
        for match in _BULLET_RE.finditer(text):
            candidate = match.group(1).strip()
            if len(candidate) >= 10:
                lessons.append(candidate)

        if self._store is not None:
            for lesson in lessons:
                self._store.add(lesson, tags=tags or [])

        return lessons

    def on_run_complete(
        self,
        blueprint: str,
        *,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Convenience wrapper: extract lessons from a completed blueprint.

        Calls :meth:`extract_lessons` and returns the list of lessons found.
        """
        return self.extract_lessons(blueprint, tags=tags)
