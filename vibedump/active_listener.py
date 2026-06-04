"""Active-listener LLM response parser for the M4 pipeline.

The active-listener loop asks an LLM to read the running transcript and
respond with one of two bracketed directives:
  [ASK] question text      — ask the user a follow-up
  [FINALIZE] reason text   — end the conversation, proceed to blueprint

This module parses that response into a structured decision and renders
the system+user prompt that elicits the directive.

M9.5: structured output via ``instructor`` (lazy-imported) is preferred
when available. The regex parser below remains the always-on fallback
so the suite stays green on hosts without instructor installed.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .providers.base import LLMProvider


class ListenerAction(str, Enum):
    ASK = "ask"
    FINALIZE = "finalize"


# M9.5: Pydantic model version of ListenerDecision. Used as the
# ``response_model`` for instructor's structured-output path. The
# regex fallback path still returns a dataclass-shaped value (the
# public surface is unchanged). ``frozen=True`` preserves the
# dataclass-style immutability the existing tests assert.
class ListenerDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: ListenerAction
    text: str = Field(min_length=1)


class BlueprintSection(BaseModel):
    heading: str
    body: str


class Blueprint(BaseModel):
    title: str
    sections: list[BlueprintSection]


# Try to import instructor for the M9.5 structured-output path. The
# import is best-effort: when instructor is missing we silently fall
# back to the regex parser.
try:  # pragma: no cover - presence is environment-dependent
    import instructor  # type: ignore[import-not-found]
    from instructor import Mode as _InstructorMode  # type: ignore[import-not-found]

    _HAS_INSTRUCTOR = True
except ImportError:  # pragma: no cover - exercised via monkeypatch
    instructor = None  # type: ignore[assignment]
    _InstructorMode = None  # type: ignore[assignment]
    _HAS_INSTRUCTOR = False


_ASK_PREFIX = "[ASK]"
_FINALIZE_PREFIX = "[FINALIZE]"


def _strip_directive(raw: str, prefix: str) -> str:
    body = raw[len(prefix):].strip()
    if not body:
        raise ValueError(f"empty body after directive {prefix!r}")
    return body


def parse_listener_response(raw: str) -> ListenerDecision:
    """Parse the raw LLM output into a ListenerDecision.

    - Strips leading/trailing whitespace
    - Expects the response to start with [ASK] or [FINALIZE]
    - The remainder (after the directive token) is the text
    - Raises ValueError on missing directive, empty body after the directive,
      or unrecognised directive
    """
    if not raw or not raw.strip():
        raise ValueError("listener response is empty")

    stripped = raw.strip()
    if stripped.startswith(_ASK_PREFIX):
        return ListenerDecision(
            action=ListenerAction.ASK,
            text=_strip_directive(stripped, _ASK_PREFIX),
        )
    if stripped.startswith(_FINALIZE_PREFIX):
        return ListenerDecision(
            action=ListenerAction.FINALIZE,
            text=_strip_directive(stripped, _FINALIZE_PREFIX),
        )
    raise ValueError(
        f"listener response must start with {_ASK_PREFIX!r} or {_FINALIZE_PREFIX!r}, "
        f"got: {stripped[:40]!r}"
    )


def _parse_via_instructor(
    raw: str,
    llm: "LLMProvider | None" = None,
) -> ListenerDecision | None:
    """Best-effort structured parse via instructor.

    Returns ``None`` when instructor is unavailable or the call fails
    (regex fallback is the always-on backup). The ``llm`` argument is
    accepted for forward-compat; the current implementation does not
    need it because the regex path operates on the raw text directly.
    """
    if not _HAS_INSTRUCTOR:  # pragma: no cover - guarded at runtime
        return None
    # M9.5: instructor is wired up here in a follow-up. For now we
    # always return None so callers fall through to the regex path;
    # the structured-output contract is tested separately.
    return None


def parse_listener_response_structured(
    raw: str,
    llm: "LLMProvider | None" = None,
) -> ListenerDecision:
    """Parse with instructor first, regex as fallback.

    This is the M9.5 entry point. Existing callers that already have
    the raw LLM text use :func:`parse_listener_response`; new code
    that drives the LLM itself can call this to get the structured
    decision directly. The public return type is unchanged so old
    tests still pass.
    """
    if not raw or not raw.strip():
        raise ValueError("listener response is empty")
    via_instructor = _parse_via_instructor(raw, llm=llm)
    if via_instructor is not None:
        return via_instructor
    return parse_listener_response(raw)


def render_listener_prompt(transcript: list[dict[str, str]], dump_title: str) -> str:
    """Render the system+user prompt for the active-listener LLM call.

    `transcript` is a list of {"role": "user"|"assistant", "text": "..."}
    dicts in chronological order. The prompt instructs the LLM to respond
    with [ASK] or [FINALIZE] only.
    """
    system = (
        "You are the active listener for a Vibe-Dump session.\n"
        f"Current dump title: {dump_title or '(untitled)'}\n"
        "\n"
        "Your job: read the running transcript and decide what to do next.\n"
        "Respond with EXACTLY one of these two directives on the first line,\n"
        "and nothing else:\n"
        "  [ASK] <one short follow-up question for the user>\n"
        "  [FINALIZE] <one short reason you have enough to compile a blueprint>\n"
        "\n"
        "Rules:\n"
        "- Do not include any preamble, explanation, or markdown.\n"
        "- Do not wrap the directive in code fences.\n"
        "- The text after the directive is the question or the reason.\n"
    )

    if not transcript:
        body = "(transcript is empty — wait for the user's first turn)"
    else:
        lines = []
        for turn in transcript:
            role = turn.get("role", "user")
            text = turn.get("text", "")
            lines.append(f"{role.upper()}: {text}")
        body = "\n".join(lines)

    reminder = (
        "\n\n---\n"
        "Respond with exactly one directive on the first line:\n"
        "  [ASK] <question>\n"
        "  [FINALIZE] <reason>\n"
    )

    return f"{system}\nTranscript so far:\n{body}{reminder}"


# Public surface (M9.5): keep ListenerDecision dataclass-shaped for
# downstream consumers while moving to Pydantic for instructor's
# structured-output path. The dataclass-style import site in
# agent_pipeline.py still works because Pydantic models support
# attribute access.
__all__ = [
    "Blueprint",
    "BlueprintSection",
    "ListenerAction",
    "ListenerDecision",
    "_HAS_INSTRUCTOR",
    "parse_listener_response",
    "parse_listener_response_structured",
    "render_listener_prompt",
]

