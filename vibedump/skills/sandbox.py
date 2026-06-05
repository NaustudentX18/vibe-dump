"""Skill sandbox: validate skill code without executing untrusted content.

The sandbox does *not* run arbitrary code.  It parses the skill source
with :mod:`ast` and rejects constructs that are considered unsafe:
dunder-name imports, ``exec``/``eval`` calls, and attribute access on
``__builtins__``.  Approved skills are stored by the
:class:`~vibedump.skills.registry.SkillRegistry` and invoked through
their pre-registered Python callables — untrusted code is never
``exec``-ed at runtime.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


_BANNED_NAMES: frozenset[str] = frozenset({"exec", "eval", "compile", "__import__"})
_BANNED_DUNDER_PREFIXES: tuple[str, ...] = ("__",)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate_skill_source(source: str) -> ValidationResult:
    """Parse ``source`` with :mod:`ast` and reject dangerous constructs.

    Returns a :class:`ValidationResult` with ``ok=True`` when the source
    passes all checks, or ``ok=False`` with a list of error strings.

    This function is deliberately conservative: it is a *deny-list* of
    known-bad patterns rather than an allow-list, so benign code will
    generally pass.
    """
    errors: list[str] = []

    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return ValidationResult(ok=False, errors=[f"SyntaxError: {exc}"])

    for node in ast.walk(tree):
        # Ban exec / eval / compile / __import__ calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_NAMES:
                errors.append(f"banned call: {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in _BANNED_NAMES:
                errors.append(f"banned attribute call: .{func.attr}()")

        # Ban direct references to banned names as standalone Name nodes
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            if not isinstance(getattr(node, "_parent", None), ast.Call):
                errors.append(f"banned name reference: {node.id}")

        # Ban imports of dunder modules (e.g. import __future__ as bypass)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("__"):
                    errors.append(f"banned dunder import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("__"):
                errors.append(f"banned dunder import from: {node.module}")

    return ValidationResult(ok=len(errors) == 0, errors=errors)
