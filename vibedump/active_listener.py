"""Active-listener LLM response parser for the M4 pipeline.

The active-listener loop asks an LLM to read the running transcript and
respond with one of two bracketed directives:
  [ASK] question text      — ask the user a follow-up
  [FINALIZE] reason text   — end the conversation, proceed to blueprint

This module parses that response into a structured decision and renders
the system+user prompt that elicits the directive.
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass


class ListenerAction(str, Enum):
    ASK = "ask"
    FINALIZE = "finalize"


@dataclass(frozen=True, slots=True)
class ListenerDecision:
    action: ListenerAction
    text: str


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
