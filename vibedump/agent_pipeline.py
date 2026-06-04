"""Agent pipeline for Vibe-Dump.

Two flows live here:

- The M0/M1 **one-shot fake flow** (kept for backward compat and
  zero-config dev): ``ingest_fake_dump`` / ``ingest_fake_into_dump`` take
  an audio path, run the (still-fake) STT, persist the user turn, and
  drop in a template blueprint.

- The M4 **active-listener flow**: ``start_dump`` creates a dump and sets
  status to LISTENING. ``add_user_turn`` appends a user turn. Each call
  to ``step_listener`` runs one LLM cycle: it asks the model for
  ``[ASK] question`` or ``[FINALIZE] reason`` and either returns the
  dump to LISTENING or runs the blueprint compiler and goes to READY.

The state machine is reflected in ``dumps.status``:
    draft -> listening -> thinking -> listening | ready
and every transition publishes a ``dump.status`` event on the bus so
the dashboard mascot can animate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .active_listener import (
    ListenerAction,
    ListenerDecision,
    parse_listener_response,
    render_listener_prompt,
)
from .database import Database, ProfileRecord, TurnRecord
from .events import EventBus
from .provider_selection import select_llm_provider
from .providers import ProviderRegistry, fake_registry
from .providers.base import LLMProvider
from .ragmemory import RagMemory
from .schemas import BLUEPRINT_SECTIONS, blueprint_template, validate_blueprint

# dumps.status values. DeviceState mirrors these but the pipeline writes
# raw strings so the DB is the source of truth.
_STATUS_LISTENING = "listening"
_STATUS_THINKING = "thinking"
_STATUS_READY = "ready"


@dataclass(slots=True)
class PipelineResult:
    dump_id: int
    transcript: str
    blueprint: str


@dataclass(slots=True)
class ListenerStepResult:
    """The outcome of one ``step_listener`` cycle."""

    dump_id: int
    action: ListenerAction
    assistant_text: str
    status: str
    blueprint: str | None = None  # populated only on FINALIZE


class AgentPipeline:
    def __init__(
        self,
        db: Database,
        registry: ProviderRegistry | None = None,
        bus: EventBus | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.db = db
        self.registry = registry or fake_registry()
        self.bus = bus
        self.memory = RagMemory(db)
        self._explicit_llm = llm
        self._resolved_llm: LLMProvider | None = None

    @property
    def llm(self) -> LLMProvider | None:
        """Resolve the LLM to use: explicit, then env/db/registry."""
        if self._explicit_llm is not None:
            return self._explicit_llm
        if self._resolved_llm is None:
            self._resolved_llm = select_llm_provider(self.registry, self.db)
        return self._resolved_llm

    # ------------------------------------------------------------------
    # M0/M1 one-shot fake flow (kept for backward compat)
    # ------------------------------------------------------------------

    def ingest_fake_dump(self, title: str, audio_path: str) -> PipelineResult:
        dump_id = self.db.create_dump(title)
        return self._populate_one_shot(dump_id, title, audio_path)

    def ingest_fake_into_dump(self, dump_id: int, audio_path: str) -> PipelineResult:
        dump = self.db.get_dump(dump_id)
        if dump is None:
            raise ValueError(f"dump {dump_id} does not exist")
        return self._populate_one_shot(dump_id, dump.title, audio_path)

    def _populate_one_shot(self, dump_id: int, title: str, audio_path: str) -> PipelineResult:
        transcript = self.registry.stt["fake"].transcribe(audio_path)
        turn_id = self.db.add_turn(dump_id, "user", transcript, audio_path=audio_path)
        self.memory.remember(dump_id, "turn", turn_id, transcript)
        blueprint = blueprint_template(title)
        blueprint_id = self.db.add_blueprint(dump_id, blueprint)
        self.memory.remember(dump_id, "blueprint", blueprint_id, blueprint)
        self._grant_xp_with_event(20, "blueprint_generated")
        self._unlock("first_blueprint")
        return PipelineResult(dump_id=dump_id, transcript=transcript, blueprint=blueprint)

    # ------------------------------------------------------------------
    # M4 active-listener flow
    # ------------------------------------------------------------------

    def start_dump(
        self,
        title: str,
        audio_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Create a new dump, optionally transcribe audio as the first turn.

        Returns the dump id. Status is set to LISTENING.
        """
        dump_id = self.db.create_dump(title, metadata)
        if audio_path:
            transcript = self.registry.stt["fake"].transcribe(audio_path)
            turn_id = self.db.add_turn(dump_id, "user", transcript, audio_path=audio_path)
            self.memory.remember(dump_id, "turn", turn_id, transcript)
        self._set_status(dump_id, _STATUS_LISTENING)
        self._grant_xp_with_event(5, "start_dump")
        self._unlock("first_dump")
        return dump_id

    def add_user_turn(self, dump_id: int, text: str) -> None:
        """Append a user turn and index it in memory."""
        if not text or not text.strip():
            raise ValueError("text cannot be empty")
        if self.db.get_dump(dump_id) is None:
            raise ValueError(f"dump {dump_id} does not exist")
        turn_id = self.db.add_turn(dump_id, "user", text)
        self.memory.remember(dump_id, "turn", turn_id, text)

    def step_listener(self, dump_id: int) -> ListenerStepResult:
        """Run one active-listener cycle.

        Sequence:
          1. Set status THINKING.
          2. Render the listener prompt from the running transcript.
          3. Call the LLM and parse [ASK] / [FINALIZE].
          4. Persist the assistant turn.
          5. On FINALIZE: run the blueprint compiler, set status READY,
             return the blueprint.
          6. On ASK: set status LISTENING, return the question.

        Raises ValueError on malformed LLM output, RuntimeError if no
        LLM is available, ValueError if the dump id is unknown.

        Threading: M4 is single-process. Two concurrent step_listener
        calls on the same dump would race on the LLM call and DB writes.
        The caller (HTTP layer) is responsible for serialising per-dump
        if it moves to a threaded server.
        """
        dump = self.db.get_dump(dump_id)
        if dump is None:
            raise ValueError(f"dump {dump_id} does not exist")
        llm = self.llm
        if llm is None:
            raise RuntimeError(
                "no LLM available: set VIBEDUMP_LLM_PROVIDER or configure a "
                "provider_config row with kind=llm and enabled=1"
            )
        transcript = _turn_dicts(self.db.list_turns(dump_id))
        prompt = render_listener_prompt(transcript, dump.title)
        self._set_status(dump_id, _STATUS_THINKING)
        raw = llm.complete(prompt)
        decision: ListenerDecision = parse_listener_response(raw)
        self.db.add_turn(dump_id, "assistant", decision.text)

        if decision.action == ListenerAction.FINALIZE:
            blueprint = self._compile_blueprint(dump_id, llm)
            self._set_status(dump_id, _STATUS_READY)
            self._grant_xp_with_event(20, "blueprint_finalize")
            self._unlock("first_blueprint")
            if self.bus is not None:
                latest = self.db.get_latest_blueprint(dump_id)
                self.bus.publish(
                    "blueprint.generated",
                    {"dump_id": dump_id, "blueprint_id": latest.id if latest else None},
                )
            return ListenerStepResult(
                dump_id=dump_id,
                action=decision.action,
                assistant_text=decision.text,
                status=_STATUS_READY,
                blueprint=blueprint,
            )

        # ASK: back to listening.
        self._set_status(dump_id, _STATUS_LISTENING)
        if self.bus is not None:
            self.bus.publish(
                "dump.assistant.ask",
                {"dump_id": dump_id, "question": decision.text},
            )
        return ListenerStepResult(
            dump_id=dump_id,
            action=decision.action,
            assistant_text=decision.text,
            status=_STATUS_LISTENING,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compile_blueprint(self, dump_id: int, llm: LLMProvider) -> str:
        """Second LLM call: turn the transcript into a Vibe Coding Blueprint.

        Falls back to the template skeleton if the LLM output is missing
        required sections — the user always gets a usable blueprint.
        """
        dump = self.db.get_dump(dump_id)
        title = dump.title if dump else ""
        transcript = _turn_dicts(self.db.list_turns(dump_id))
        prompt = _render_blueprint_prompt(transcript, title)
        raw = llm.complete(prompt)
        if validate_blueprint(raw):
            raw = blueprint_template(title)
        blueprint_id = self.db.add_blueprint(dump_id, raw)
        self.memory.remember(dump_id, "blueprint", blueprint_id, raw)
        return raw

    def _set_status(self, dump_id: int, status: str) -> None:
        self.db.update_dump_status(dump_id, status)
        if self.bus is not None:
            self.bus.publish("dump.status", {"dump_id": dump_id, "status": status})

    def _grant_xp_with_event(self, amount: int, reason: str) -> ProfileRecord | None:
        """Grant XP, publish events, and unlock level/xp milestones.

        Returns the updated profile, or None if amount is non-positive.
        Emits ``profile.xp`` on every grant and ``profile.level_up`` only
        when the level actually changes. Milestone unlocks (``level_2``,
        ``xp_100``, ``streak_3``) are checked and emitted via ``_unlock``.
        """
        if amount <= 0:
            return None
        before = self.db.get_profile()
        profile = self.db.grant_xp(amount)
        if self.bus is not None:
            self.bus.publish(
                "profile.xp",
                {
                    "xp": profile.xp,
                    "level": profile.level,
                    "delta": amount,
                    "reason": reason,
                },
            )
        if profile.level > before.level and self.bus is not None:
            self.bus.publish("profile.level_up", {"level": profile.level})
        if profile.level >= 2:
            self._unlock("level_2")
        if profile.xp >= 100:
            self._unlock("xp_100")
        if profile.streak_days >= 3:
            self._unlock("streak_3")
        return profile

    def _unlock(self, key: str) -> None:
        """Unlock an achievement and publish on first unlock.

        Idempotent: already-unlocked or unknown keys are silent no-ops.
        """
        if not self.db.unlock_achievement(key):
            return
        title = next(
            (a.title for a in self.db.list_achievements() if a.key == key),
            key,
        )
        if self.bus is not None:
            self.bus.publish(
                "achievement.unlocked",
                {"key": key, "title": title},
            )


# Backward-compat alias. The class was previously named FakeAgentPipeline
# because the whole pipeline was a single fake step. It is now a real
# active-listener pipeline that delegates to (still-fake) STT/TTS.
FakeAgentPipeline = AgentPipeline


def _turn_dicts(turns: list[TurnRecord]) -> list[dict[str, str]]:
    return [{"role": t.role, "text": t.text} for t in turns]


def _render_blueprint_prompt(transcript: list[dict[str, str]], title: str) -> str:
    """Render the prompt that asks the LLM to compile the blueprint."""
    sections = "\n".join(f"- {i+1}. {name}" for i, name in enumerate(BLUEPRINT_SECTIONS))
    system = (
        "You are the blueprint compiler for Vibe-Dump.\n"
        f"Dump title: {title or '(untitled)'}\n"
        "\n"
        "Given the transcript below, produce a complete Vibe Coding Blueprint "
        "in markdown. The blueprint MUST include every section, in order:\n"
        f"{sections}\n\n"
        "Rules:\n"
        "- Fill each section with what the transcript actually says.\n"
        "- If a section has no direct content, write a one-line note saying so.\n"
        "- Do not include any preamble, explanation, or code fences.\n"
    )
    if transcript:
        body = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)
    else:
        body = "(transcript is empty)"
    return f"{system}\n\nTranscript:\n{body}"


__all__ = ["AgentPipeline", "FakeAgentPipeline", "ListenerStepResult", "PipelineResult"]
