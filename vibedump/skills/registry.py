"""SkillRegistry: register and invoke named callable skills.

Skills are pre-vetted Python callables (not arbitrary source strings).
The :class:`SkillRegistry` pairs them with optional source-level
validation via :mod:`~vibedump.skills.sandbox` so operators can verify
that the skill code they deploy meets the policy before it is registered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .sandbox import ValidationResult, validate_skill_source


@dataclass(frozen=True, slots=True)
class SkillEntry:
    name: str
    description: str
    handler: Callable[..., Any]
    source: str = ""


class SkillRegistry:
    """Register and invoke named skills.

    Parameters
    ----------
    validate_on_register:
        When ``True`` (default) any ``source`` string provided to
        :meth:`register` is validated by the sandbox before the skill is
        accepted.  Pass ``False`` in tests where you want to skip source
        validation.
    """

    def __init__(self, *, validate_on_register: bool = True) -> None:
        self._skills: dict[str, SkillEntry] = {}
        self._validate = validate_on_register

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        description: str = "",
        source: str = "",
    ) -> None:
        """Register a skill.

        Parameters
        ----------
        name:
            Unique skill identifier.
        handler:
            The callable to invoke when the skill is run.
        description:
            Human-readable description.
        source:
            Optional source string to validate through the sandbox.
            If ``validate_on_register`` is ``True`` and ``source`` is
            non-empty, a :class:`ValueError` is raised when the source
            fails validation.
        """
        if name in self._skills:
            raise ValueError(f"skill {name!r} is already registered")
        if source and self._validate:
            result = validate_skill_source(source)
            if not result.ok:
                raise ValueError(
                    f"skill {name!r} failed sandbox validation: {result.errors}"
                )
        self._skills[name] = SkillEntry(
            name=name,
            description=description,
            handler=handler,
            source=source,
        )

    def unregister(self, name: str) -> bool:
        """Remove a skill.  Returns True if it existed."""
        return self._skills.pop(name, None) is not None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, name: str, **kwargs: Any) -> Any:
        """Invoke a registered skill by name.

        Raises
        ------
        KeyError
            When the skill is not registered.
        """
        if name not in self._skills:
            raise KeyError(f"unknown skill {name!r}")
        return self._skills[name].handler(**kwargs)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
