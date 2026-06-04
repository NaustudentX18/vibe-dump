"""Tests for the M4 active-listener pipeline (AgentPipeline).

The pipeline owns the state machine:

    IDLE  ->  LISTENING  ->  THINKING  ->  LISTENING | READY

`step_listener` is one cycle: it asks the LLM for [ASK] or [FINALIZE],
persists the assistant turn, and either returns to LISTENING (waiting
for the next user turn) or runs the blueprint compiler and goes to
READY. The HTTP layer (or a future agent loop) is responsible for
calling `step_listener` again after each user turn.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from vibedump.active_listener import ListenerAction
from vibedump.agent_pipeline import AgentPipeline
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import LLMProvider, ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS
from vibedump.schemas import blueprint_template, validate_blueprint


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """A stub LLM whose complete() returns a scripted sequence of responses."""

    def __init__(self, *responses: str, name: str = "scripted") -> None:
        self._responses: list[str] = list(responses)
        self._calls: list[str] = []
        self.name = name
        self.configured = True

    def complete(self, prompt: str) -> str:
        self._calls.append(prompt)
        if not self._responses:
            raise AssertionError("scripted LLM ran out of responses")
        return self._responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted stub ready")


def _registry_with(llm: LLMProvider) -> ProviderRegistry:
    """Registry with the scripted LLM plus the fake STT/TTS the pipeline uses."""
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm[llm.name] = llm
    registry.tts["fake"] = FakeTTS()
    return registry


@pytest.fixture
def pipeline() -> Iterator[tuple[AgentPipeline, _ScriptedLLM, Database, EventBus]]:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    llm = _ScriptedLLM()
    p = AgentPipeline(db, _registry_with(llm), bus=bus, llm=llm)
    yield p, llm, db, bus
    db.close()


def _missing_sections(md: str) -> list[str]:
    return validate_blueprint(md)


# ---------------------------------------------------------------------------
# start_dump
# ---------------------------------------------------------------------------


def test_start_dump_creates_dump_in_listening_state(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    dump_id = p.start_dump("Test Dump")
    dump = db.get_dump(dump_id)
    assert dump is not None
    assert dump.title == "Test Dump"
    assert dump.status == "listening"


def test_start_dump_with_audio_transcribes_and_adds_user_turn(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    turns = db.list_turns(dump_id)
    assert len(turns) == 1
    assert turns[0].role == "user"
    assert "idea.wav" in turns[0].text
    assert turns[0].audio_path == "idea.wav"


def test_start_dump_publishes_status_to_event_bus(pipeline: Any) -> None:
    p, _, _, bus = pipeline
    p.start_dump("Test")
    statuses = [e.payload["status"] for e in bus.snapshot() if e.event_type == "dump.status"]
    assert statuses == ["listening"]


# ---------------------------------------------------------------------------
# add_user_turn
# ---------------------------------------------------------------------------


def test_add_user_turn_appends_turn(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    dump_id = p.start_dump("Test")
    p.add_user_turn(dump_id, "actually, it's mobile-first")
    turns = db.list_turns(dump_id)
    assert len(turns) == 1
    assert turns[0].text == "actually, it's mobile-first"
    assert turns[0].role == "user"


def test_add_user_turn_unknown_dump_raises(pipeline: Any) -> None:
    p, _, _, _ = pipeline
    with pytest.raises(ValueError, match="dump 9999 does not exist"):
        p.add_user_turn(9999, "hi")


def test_add_user_turn_empty_text_raises(pipeline: Any) -> None:
    p, _, _, _ = pipeline
    dump_id = p.start_dump("Test")
    with pytest.raises(ValueError, match="text cannot be empty"):
        p.add_user_turn(dump_id, "")
    with pytest.raises(ValueError, match="text cannot be empty"):
        p.add_user_turn(dump_id, "   ")


def test_start_dump_persists_metadata(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    dump_id = p.start_dump("Test", metadata={"client": "ios", "version": 1})
    dump = db.get_dump(dump_id)
    assert dump.metadata == {"client": "ios", "version": 1}


# ---------------------------------------------------------------------------
# step_listener: errors and basic shape
# ---------------------------------------------------------------------------


def test_step_listener_without_llm_raises_clear_error() -> None:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    p = AgentPipeline(db, ProviderRegistry(), bus=bus)  # no llm passed + empty registry
    dump_id = p.start_dump("Test")
    with pytest.raises(RuntimeError, match="no LLM available"):
        p.step_listener(dump_id)
    db.close()


def test_step_listener_unknown_dump_raises(pipeline: Any) -> None:
    p, _, _, _ = pipeline
    with pytest.raises(ValueError, match="dump 9999 does not exist"):
        p.step_listener(9999)


def test_step_listener_malformed_output_raises(pipeline: Any) -> None:
    p, llm, _, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    llm._responses = ["oh hi there, no directive here"]  # parser will reject
    with pytest.raises(ValueError, match=r"\[ASK\]|\[FINALIZE\]"):
        p.step_listener(dump_id)


# ---------------------------------------------------------------------------
# step_listener: ASK path
# ---------------------------------------------------------------------------


def test_step_listener_ask_response_persists_assistant_turn_and_returns_to_listening(
    pipeline: Any,
) -> None:
    p, llm, db, bus = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    llm._responses = ["[ASK] what platform should this run on?"]

    result = p.step_listener(dump_id)

    assert result.action == ListenerAction.ASK
    assert result.assistant_text == "what platform should this run on?"
    assert result.status == "listening"
    assert result.blueprint is None
    assert db.get_dump(dump_id).status == "listening"

    turns = db.list_turns(dump_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[1].text == "what platform should this run on?"

    statuses = [e.payload["status"] for e in bus.snapshot() if e.event_type == "dump.status"]
    assert statuses == ["listening", "thinking", "listening"]


# ---------------------------------------------------------------------------
# step_listener: FINALIZE path
# ---------------------------------------------------------------------------


def test_step_listener_finalize_runs_blueprint_compiler_and_marks_ready(
    pipeline: Any,
) -> None:
    p, llm, db, bus = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    # Two scripted LLM calls: listener says FINALIZE, then compiler runs.
    llm._responses = [
        "[FINALIZE] we have enough context",
        blueprint_template("Test"),
    ]

    result = p.step_listener(dump_id)

    assert result.action == ListenerAction.FINALIZE
    assert result.assistant_text == "we have enough context"
    assert result.status == "ready"
    assert result.blueprint is not None
    assert _missing_sections(result.blueprint) == []

    bp = db.get_latest_blueprint(dump_id)
    assert bp is not None
    assert bp.markdown == result.blueprint

    assert db.get_dump(dump_id).status == "ready"

    turns = db.list_turns(dump_id)
    assert [t.role for t in turns] == ["user", "assistant"]

    statuses = [e.payload["status"] for e in bus.snapshot() if e.event_type == "dump.status"]
    assert statuses == ["listening", "thinking", "ready"]


def test_blueprint_compiler_falls_back_to_template_on_bad_output(pipeline: Any) -> None:
    p, llm, db, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    llm._responses = [
        "[FINALIZE] done",
        "this is not a valid blueprint at all",  # compiler gets garbage
    ]

    result = p.step_listener(dump_id)

    assert result.status == "ready"
    assert result.blueprint is not None
    # Falls back to template: all sections present.
    assert _missing_sections(result.blueprint) == []
    bp = db.get_latest_blueprint(dump_id)
    assert bp is not None


def test_blueprint_compiler_falls_back_on_partial_output(pipeline: Any) -> None:
    """A near-perfect LLM run that misses one section is replaced wholesale.

    Documents the design choice: any missing section triggers the full
    template fallback. If a future iteration wants to merge LLM content
    with the template for missing sections, this test will need to change.
    """
    p, llm, db, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    # Build a blueprint that's missing the last section.
    full = blueprint_template("Test").splitlines()
    truncated = "\n".join(full[:-2]) + "\n"  # drops the final "TBD" line
    llm._responses = ["[FINALIZE] done", truncated]

    result = p.step_listener(dump_id)

    assert result.status == "ready"
    assert _missing_sections(result.blueprint) == []  # fallback filled the gap
    assert db.get_latest_blueprint(dump_id).markdown == result.blueprint


# ---------------------------------------------------------------------------
# Full loop: ASK then user turn then FINALIZE
# ---------------------------------------------------------------------------


def test_full_loop_with_multiple_user_turns(pipeline: Any) -> None:
    p, llm, db, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    # First listener call: ask
    llm._responses = ["[ASK] what platform?"]
    r1 = p.step_listener(dump_id)
    assert r1.action == ListenerAction.ASK
    assert db.get_dump(dump_id).status == "listening"

    # User responds
    p.add_user_turn(dump_id, "iOS first, then Android")

    # Second listener call: finalize
    llm._responses = ["[FINALIZE] enough info", blueprint_template("Test")]
    r2 = p.step_listener(dump_id)
    assert r2.action == ListenerAction.FINALIZE
    assert r2.status == "ready"
    assert db.get_dump(dump_id).status == "ready"

    turns = db.list_turns(dump_id)
    assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]


def test_step_listener_passes_transcript_including_prior_assistant_turns(
    pipeline: Any,
) -> None:
    """The LLM is given the running transcript, not just the last user turn."""
    p, llm, _, _ = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    llm._responses = ["[ASK] what platform?"]
    p.step_listener(dump_id)
    p.add_user_turn(dump_id, "iOS first")
    llm._responses = ["[FINALIZE] done", blueprint_template("Test")]
    p.step_listener(dump_id)

    # The second listener call's prompt should include both the first
    # assistant question and the user's follow-up.
    second_prompt = llm._calls[1]
    assert "what platform?" in second_prompt
    assert "iOS first" in second_prompt


# ---------------------------------------------------------------------------
# Backward compat: old one-shot fake flow still works
# ---------------------------------------------------------------------------


def test_backward_compat_ingest_fake_dump_still_works(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    result = p.ingest_fake_dump("Test", "idea.wav")
    assert result.dump_id > 0
    assert "idea.wav" in result.transcript
    assert _missing_sections(result.blueprint) == []
    bp = db.get_latest_blueprint(result.dump_id)
    assert bp is not None


def test_backward_compat_ingest_fake_into_dump_still_works(pipeline: Any) -> None:
    p, _, db, _ = pipeline
    dump_id = p.start_dump("Test")
    result = p.ingest_fake_into_dump(dump_id, "idea.wav")
    assert result.dump_id == dump_id
    assert "idea.wav" in result.transcript
