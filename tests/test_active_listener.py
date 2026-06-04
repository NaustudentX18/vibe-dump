"""Tests for the M4 active-listener LLM response parser."""

from __future__ import annotations

import pytest

from vibedump.active_listener import (
    ListenerAction,
    ListenerDecision,
    parse_listener_response,
    render_listener_prompt,
)


class TestParseAskDirective:
    def test_simple_ask(self) -> None:
        decision = parse_listener_response("[ASK] What platform?")
        assert decision == ListenerDecision(action=ListenerAction.ASK, text="What platform?")

    def test_ask_preserves_multi_line_text(self) -> None:
        raw = "[ASK] What platform?\nAnd what language?"
        decision = parse_listener_response(raw)
        assert decision.action is ListenerAction.ASK
        assert decision.text == "What platform?\nAnd what language?"

    def test_ask_with_extra_spaces_after_directive(self) -> None:
        decision = parse_listener_response("[ASK]    What platform?")
        assert decision.text == "What platform?"


class TestParseFinalizeDirective:
    def test_simple_finalize(self) -> None:
        decision = parse_listener_response("[FINALIZE] enough info")
        assert decision == ListenerDecision(
            action=ListenerAction.FINALIZE, text="enough info"
        )

    def test_finalize_with_multi_line_text(self) -> None:
        raw = "[FINALIZE] got everything I need:\n- platform\n- lang"
        decision = parse_listener_response(raw)
        assert decision.action is ListenerAction.FINALIZE
        assert decision.text == "got everything I need:\n- platform\n- lang"


class TestWhitespaceTolerance:
    def test_leading_newline_stripped(self) -> None:
        decision = parse_listener_response("\n[ASK] What platform?")
        assert decision == ListenerDecision(action=ListenerAction.ASK, text="What platform?")

    def test_trailing_whitespace_stripped(self) -> None:
        decision = parse_listener_response("[ASK] What platform?   \n")
        assert decision.text == "What platform?"

    def test_leading_and_trailing_whitespace_stripped(self) -> None:
        decision = parse_listener_response("  \n[FINALIZE] done   \n")
        assert decision.action is ListenerAction.FINALIZE
        assert decision.text == "done"


class TestParseErrors:
    def test_empty_body_after_ask_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("[ASK]")

    def test_empty_body_after_finalize_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("[FINALIZE]   ")

    def test_only_whitespace_body_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("[ASK]   \n  \t  ")

    def test_missing_directive_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("What platform?")

    def test_unknown_directive_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("[DONE] x")

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("")

    def test_whitespace_only_input_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_listener_response("   \n\t  ")


class TestDecisionImmutability:
    def test_decision_is_frozen(self) -> None:
        decision = parse_listener_response("[ASK] hi")
        with pytest.raises(Exception):
            decision.action = ListenerAction.FINALIZE  # type: ignore[misc]


class TestRenderListenerPrompt:
    def test_includes_dump_title(self) -> None:
        prompt = render_listener_prompt(
            transcript=[{"role": "user", "text": "I want a todo app"}],
            dump_title="Robot Todo",
        )
        assert "Robot Todo" in prompt

    def test_includes_transcript(self) -> None:
        prompt = render_listener_prompt(
            transcript=[
                {"role": "user", "text": "I want a todo app"},
                {"role": "assistant", "text": "What platform?"},
                {"role": "user", "text": "iOS"},
            ],
            dump_title="Robot Todo",
        )
        assert "I want a todo app" in prompt
        assert "What platform?" in prompt
        assert "iOS" in prompt

    def test_ends_with_directive_reminder(self) -> None:
        prompt = render_listener_prompt(
            transcript=[{"role": "user", "text": "hi"}],
            dump_title="t",
        )
        # The final lines should remind the model how to respond.
        assert "[ASK]" in prompt
        assert "[FINALIZE]" in prompt

    def test_empty_transcript_does_not_crash(self) -> None:
        prompt = render_listener_prompt(transcript=[], dump_title="Empty")
        # The title still shows up, the directive reminder still shows up.
        assert "Empty" in prompt
        assert "[ASK]" in prompt
        assert "[FINALIZE]" in prompt


# ---------------------------------------------------------------------------
# M9.5: Pydantic models + structured-output fallback
# ---------------------------------------------------------------------------


class TestM95StructuredOutput:
    """M9.5 ships Pydantic models for instructor's structured-output path.

    The regex parser remains the always-on fallback. These tests pin
    the new surface: the Pydantic models are importable, the regex
    path still wins when instructor is unavailable, and the
    structured entry point returns the same public type.
    """

    def test_listener_decision_is_pydantic(self) -> None:
        from vibedump.active_listener import (
            Blueprint,
            BlueprintSection,
            ListenerDecision,
        )

        # The Pydantic shape: keyword-only init, validation on assignment.
        d = ListenerDecision(action=ListenerAction.ASK, text="hi")
        assert d.action is ListenerAction.ASK
        assert d.text == "hi"
        with pytest.raises(Exception):
            d.text = "no"  # type: ignore[misc]

        section = BlueprintSection(heading="Goal", body="one-liner")
        bp = Blueprint(title="t", sections=[section])
        assert bp.sections[0].heading == "Goal"

    def test_structured_path_falls_back_to_regex_when_instructor_missing(
        self, monkeypatch
    ) -> None:
        from vibedump.active_listener import (
            _HAS_INSTRUCTOR,
            parse_listener_response_structured,
        )

        # Pretend instructor is not installed: the public helper must
        # transparently fall through to the regex parser.
        monkeypatch.setattr(
            "vibedump.active_listener._HAS_INSTRUCTOR", False
        )
        if _HAS_INSTRUCTOR:
            pytest.skip("instructor is installed; can't simulate missing")
        decision = parse_listener_response_structured("[ASK] what platform?")
        assert decision.action is ListenerAction.ASK
        assert decision.text == "what platform?"

    def test_structured_path_uses_instructor_when_available(self, monkeypatch) -> None:
        from vibedump.active_listener import ListenerAction, parse_listener_response_structured

        # Patch in a fake instructor parser that returns a structured
        # ListenerDecision without going through the LLM.
        def fake_parse(raw, llm=None):
            return ListenerDecision(action=ListenerAction.ASK, text="instructor-said-hi")

        monkeypatch.setattr(
            "vibedump.active_listener._parse_via_instructor", fake_parse
        )
        monkeypatch.setattr(
            "vibedump.active_listener._HAS_INSTRUCTOR", True
        )
        decision = parse_listener_response_structured("[ASK] what platform?")
        assert decision.text == "instructor-said-hi"

    def test_structured_path_rejects_empty(self) -> None:
        from vibedump.active_listener import parse_listener_response_structured

        with pytest.raises(ValueError, match="empty"):
            parse_listener_response_structured("")
        with pytest.raises(ValueError, match="empty"):
            parse_listener_response_structured("   ")
