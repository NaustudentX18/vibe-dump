"""Tests for vibedump.skills (SkillRegistry and sandbox)."""

from __future__ import annotations

import pytest

from vibedump.skills import (
    SkillRegistry,
    ValidationResult,
    validate_skill_source,
)


# ---------------------------------------------------------------------------
# SkillRegistry: register / run / introspect
# ---------------------------------------------------------------------------


def test_register_and_run_skill():
    reg = SkillRegistry()
    reg.register("add", lambda x, y: x + y, description="add two numbers")
    assert "add" in reg
    assert len(reg) == 1
    assert reg.run("add", x=3, y=4) == 7


def test_register_duplicate_raises():
    reg = SkillRegistry()
    reg.register("ping", lambda: "pong")
    with pytest.raises(ValueError, match="already registered"):
        reg.register("ping", lambda: "pong2")


def test_run_unknown_skill_raises():
    reg = SkillRegistry()
    with pytest.raises(KeyError, match="unknown skill"):
        reg.run("nonexistent")


def test_unregister_skill():
    reg = SkillRegistry()
    reg.register("temp", lambda: None)
    assert reg.unregister("temp") is True
    assert "temp" not in reg
    assert reg.unregister("temp") is False


def test_names_sorted():
    reg = SkillRegistry()
    reg.register("zebra", lambda: None)
    reg.register("apple", lambda: None)
    assert reg.names() == ["apple", "zebra"]


# ---------------------------------------------------------------------------
# Sandbox validation
# ---------------------------------------------------------------------------


def test_valid_source_passes():
    source = "def greet(name):\n    return f'Hello, {name}'\n"
    result = validate_skill_source(source)
    assert result.ok
    assert result.errors == []


def test_exec_call_blocked():
    source = "exec('import os; os.system(\"rm -rf /\")')"
    result = validate_skill_source(source)
    assert not result.ok
    assert any("exec" in e for e in result.errors)


def test_eval_call_blocked():
    source = "x = eval('1+1')"
    result = validate_skill_source(source)
    assert not result.ok
    assert any("eval" in e for e in result.errors)


def test_syntax_error_returns_not_ok():
    result = validate_skill_source("def broken(:\n    pass")
    assert not result.ok
    assert any("SyntaxError" in e for e in result.errors)


def test_registry_rejects_bad_source():
    reg = SkillRegistry(validate_on_register=True)

    def safe_fn():
        return "ok"

    with pytest.raises(ValueError, match="sandbox validation"):
        reg.register("evil", safe_fn, source="exec('oops')")


def test_registry_skip_validation():
    reg = SkillRegistry(validate_on_register=False)

    def safe_fn():
        return "ok"

    # With validation off, even a source string containing exec is allowed.
    reg.register("risky", safe_fn, source="exec('oops')")
    assert "risky" in reg
