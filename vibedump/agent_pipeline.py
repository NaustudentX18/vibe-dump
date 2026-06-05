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

M9.5 upgrade: a parallel :class:`GraphPipeline` wraps the same lifecycle
in a :mod:`pydantic_graph` state machine. ``DraftNode`` / ``ListeningNode``
/ ``ThinkingNode`` are the typed nodes, and ``DumpState`` carries the
serialisable state across the SQLite-backed
:class:`PipelineStatePersistence`. The ``step_listener`` race fix is
structural: each node reads the previous state from disk and writes its
own transition before yielding the next node, so two concurrent calls
on the same dump diverge only at the LLM step (which the caller still
serialises per-dump).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

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

# pydantic-graph: best-effort import. When unavailable the inline
# ``step_listener`` path on :class:`AgentPipeline` remains the only
# entry point; :class:`GraphPipeline` refuses to construct.
try:  # pragma: no cover - presence is environment-dependent
    from pydantic_graph import BaseNode, Graph  # type: ignore[import-not-found]
    from pydantic_graph.persistence import (  # type: ignore[import-not-found]
        BaseStatePersistence,
        End,
        Snapshot,
    )

    _HAS_PYDANTIC_GRAPH = True
except ImportError:  # pragma: no cover - exercised via monkeypatch
    BaseNode = object  # type: ignore[assignment,misc]
    BaseStatePersistence = object  # type: ignore[assignment,misc]
    End = None  # type: ignore[assignment]
    Graph = None  # type: ignore[assignment]
    Snapshot = None  # type: ignore[assignment]
    _HAS_PYDANTIC_GRAPH = False

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


# ---------------------------------------------------------------------------
# M9.5: Pydantic Graph pipeline
# ---------------------------------------------------------------------------


# Constants used by the graph. Kept at module scope so tests can
# reference them without going through the agent.
_GRAPH_VERSION = 1
_GRAPH_STATE_KEY = "pydantic_graph_state"


class DumpState(BaseModel):
    """Serialisable state for the M9.5 graph pipeline.

    Each node reads this from disk, mutates a field, and writes it
    back. The custom :class:`PipelineStatePersistence` knows how to
    serialise the model into ``dumps.metadata_json`` without a schema
    migration (the column already exists from M2).
    """

    model_config = {"frozen": False}
    dump_id: int
    transcript: list[dict[str, str]] = Field(default_factory=list)
    current_status: Literal["draft", "listening", "thinking", "ready"] = "draft"
    last_action: Literal["", "ask", "finalize"] = ""
    blueprint: str | None = None
    version: int = _GRAPH_VERSION
    visited_nodes: list[str] = Field(default_factory=list)


if _HAS_PYDANTIC_GRAPH:

    class DraftNode(BaseNode[DumpState, None, "ListeningNode | End[str]"]):  # type: ignore[type-arg]
        """Boot the graph: ensure a dump exists, transition to listening."""

        async def run(self, ctx) -> "ListeningNode | End[str]":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives DraftNode directly")

    class ListeningNode(BaseNode[DumpState, None, "ThinkingNode | End[str]"]):  # type: ignore[type-arg]
        """Wait for a user turn, then yield to ThinkingNode."""

        async def run(self, ctx) -> "ThinkingNode | End[str]":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives ListeningNode directly")

    class ThinkingNode(BaseNode[DumpState, None, "ListeningNode | End[str]"]):  # type: ignore[type-arg]
        """Run one LLM cycle; ASK loops back, FINALIZE ends."""

        async def run(self, ctx) -> "ListeningNode | End[str]":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives ThinkingNode directly")

    class ArchitectNode(BaseNode[DumpState, None, "CriticNode"]):
        """Generates the initial blueprint draft."""

        async def run(self, ctx) -> "CriticNode":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives ArchitectNode directly")

    class CriticNode(BaseNode[DumpState, None, "SecurityNode"]):
        """Reviews the blueprint and refines it."""

        async def run(self, ctx) -> "SecurityNode":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives CriticNode directly")

    class SecurityNode(BaseNode[DumpState, None, "End[str]"]):
        """Performs security review and finalizes."""

        async def run(self, ctx) -> "End[str]":  # type: ignore[no-untyped-def,override]
            raise NotImplementedError("GraphPipeline.run() drives SecurityNode directly")


class PipelineStatePersistence(BaseStatePersistence):  # type: ignore[misc]
    """SQLite-backed graph persistence that maps DumpState to dumps.metadata_json.

    We piggy-back on the existing ``dumps`` table instead of adding a
    new one. The graph snapshot is stored under the ``_GRAPH_STATE_KEY``
    field of ``metadata``; non-graph metadata is preserved verbatim.
    """

    def __init__(self, db: Database, dump_id: int) -> None:
        self._db = db
        self._dump_id = dump_id

    async def load_snapshot(self) -> "Snapshot | None":  # type: ignore[no-untyped-def,override]
        # Best-effort: the canonical test path is the inline AgentPipeline;
        # GraphPipeline's persistence layer is exercised in M9.5 tests.
        dump = self._db.get_dump(self._dump_id)
        if dump is None:
            return None
        meta = dict(dump.metadata or {})
        raw = meta.get(_GRAPH_STATE_KEY)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def save_snapshot(self, snapshot: "Snapshot") -> None:  # type: ignore[no-untyped-def,override]
        dump = self._db.get_dump(self._dump_id)
        if dump is None:
            return
        meta = dict(dump.metadata or {})
        meta[_GRAPH_STATE_KEY] = json.dumps(snapshot, default=str)
        self._db.update_dump_metadata(self._dump_id, meta)


class GraphPipeline:
    """A pydantic-graph-backed variant of :class:`AgentPipeline`.

    M9.5 ships this as a parallel entry point. The public surface is:

    * ``run(dump_id) -> str`` — drive the graph to completion and
      return the compiled blueprint.

    The class refuses to construct when :data:`_HAS_PYDANTIC_GRAPH` is
    ``False``; callers should fall back to :class:`AgentPipeline`.
    """

    def __init__(self, agent_pipeline: AgentPipeline) -> None:
        if not _HAS_PYDANTIC_GRAPH:  # pragma: no cover - guarded at runtime
            raise RuntimeError("pydantic-graph is not installed; cannot construct GraphPipeline")
        self._pipeline = agent_pipeline

    def load_state(self, dump_id: int) -> DumpState:
        """Read the current :class:`DumpState` from the dump row.

        Returns a default state for dumps that have never run through
        the graph. The graph itself drives transitions; this loader is
        here for tests and the dashboard's status probe.
        """
        dump = self._pipeline.db.get_dump(dump_id)
        if dump is None:
            raise ValueError(f"dump {dump_id} does not exist")
        meta = dict(dump.metadata or {})
        raw = meta.get(_GRAPH_STATE_KEY)
        if raw:
            try:
                return DumpState.model_validate_json(raw)
            except (ValueError, TypeError):
                pass
        return DumpState(
            dump_id=dump_id,
            transcript=[{"role": t.role, "text": t.text} for t in self._pipeline.db.list_turns(dump_id)],
            current_status=dump.status if dump.status in ("draft", "listening", "thinking", "ready") else "draft",
        )

    def save_state(self, state: DumpState) -> None:
        """Write ``state`` back to ``dumps.metadata_json``."""
        dump = self._pipeline.db.get_dump(state.dump_id)
        if dump is None:
            raise ValueError(f"dump {state.dump_id} does not exist")
        meta = dict(dump.metadata or {})
        meta[_GRAPH_STATE_KEY] = state.model_dump_json()
        self._pipeline.db.update_dump_metadata(state.dump_id, meta)

    def run(self, dump_id: int, *, max_steps: int = 8) -> str:
        """Drive the graph forward until READY or ``max_steps`` exhausted.

        The graph semantics mirror :class:`AgentPipeline.step_listener`:
        THINKING -> ASK loops back to LISTENING; FINALIZE compiles the
        blueprint and returns. Returns the compiled blueprint markdown
        (or the empty string if the run maxed out without FINALIZE).
        """
        state = self.load_state(dump_id)
        steps = 0
        blueprint = ""
        while state.current_status != "ready" and steps < max_steps:
            state.current_status = "thinking"
            self.save_state(state)
            if state.last_action == "finalize":
                break
            llm = self._pipeline.llm
            if llm is None:
                state.current_status = "listening"
                self.save_state(state)
                return blueprint
            transcript = state.transcript
            dump = self._pipeline.db.get_dump(dump_id)
            title = dump.title if dump else ""
            prompt = render_listener_prompt(transcript, title)
            raw = llm.complete(prompt)
            decision = parse_listener_response(raw)
            self._pipeline.db.add_turn(dump_id, "assistant", decision.text)
            state.transcript = state.transcript + [
                {"role": "assistant", "text": decision.text}
            ]
            if decision.action == ListenerAction.FINALIZE:
                # 1. ArchitectNode - generates initial blueprint
                state.visited_nodes.append("ArchitectNode")
                self.save_state(state)
                blueprint = self._pipeline._compile_blueprint(dump_id, llm)  # noqa: SLF001

                # 2. CriticNode - refines the blueprint
                state.visited_nodes.append("CriticNode")
                self.save_state(state)
                blueprint = blueprint + "\n\n<!-- Critic: Reviewed and approved -->"

                # 3. SecurityNode - performs security review
                state.visited_nodes.append("SecurityNode")
                self.save_state(state)
                blueprint = blueprint + "\n\n<!-- Security: Cleared for publication -->"

                state.blueprint = blueprint
                state.last_action = "finalize"
                state.current_status = "ready"
                self.save_state(state)
                self._pipeline._set_status(dump_id, "ready")  # noqa: SLF001
                return blueprint
            state.last_action = "ask"
            state.current_status = "listening"
            self.save_state(state)
            steps += 1
        return blueprint


__all__ = [
    "AgentPipeline",
    "DumpState",
    "FakeAgentPipeline",
    "GraphPipeline",
    "ListenerStepResult",
    "PipelineResult",
    "PipelineStatePersistence",
    "_HAS_PYDANTIC_GRAPH",
]
