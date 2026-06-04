"""Regression checks for the M5 dashboard markup.

Static HTML+JS is hard to pytest directly without a browser, so this file
asserts on the source string of ``vibedump/static/dashboard.html``: that it
parses cleanly, contains the new M5 hooks, and that no obvious markers
regressed when the file is modified.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "vibedump" / "static" / "dashboard.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert DASHBOARD_PATH.exists(), f"dashboard.html missing at {DASHBOARD_PATH}"
    text = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert text, "dashboard.html is empty"
    return text


def test_dashboard_file_is_non_empty_and_readable(html: str) -> None:
    assert len(html) > 1000, "dashboard.html shrank unexpectedly"


def test_dashboard_has_meta_and_viewport(html: str) -> None:
    assert re.search(r'<meta\s+charset="utf-8"', html, re.IGNORECASE)
    assert re.search(r'<meta\s+name="viewport"', html, re.IGNORECASE)


def test_dashboard_mascot_uses_img_tag(html: str) -> None:
    # The CSS-only mascot div (with .body/.eye/.mouth) was replaced by an <img>.
    assert re.search(r'<img\s+id="mascotImg"', html), "mascot <img> element missing"
    # Old child divs should be gone (anchored to the #mascot element only).
    m = re.search(r'<div\s+class="mascot"[^>]*id="mascot"[^>]*>(.*?)</div>', html, re.DOTALL)
    assert m, "mascot parent div not found"
    inner = m.group(1)
    assert "<img" in inner, "mascot parent must contain an <img>"
    assert "class=\"body\"" not in inner, "stale .body element still present in mascot"
    assert "class=\"eye" not in inner, "stale .eye element still present in mascot"
    assert "class=\"mouth\"" not in inner, "stale .mouth element still present in mascot"


def test_dashboard_references_achievements_endpoint(html: str) -> None:
    assert "/api/achievements" in html


def test_dashboard_references_profile_endpoint(html: str) -> None:
    assert "/api/profile" in html
    assert "api(API.profile" in html or "method: \"PATCH\"" in html
    # Must use PATCH (not POST/GET) for the profile name update.
    assert re.search(r'method:\s*"PATCH".*api/profile|api/profile.*method:\s*"PATCH"', html, re.DOTALL) \
        or 'method: "PATCH"' in html and "/api/profile" in html


def test_dashboard_handles_achievement_unlocked_sse(html: str) -> None:
    assert "achievement.unlocked" in html
    # Listener must actually subscribe to the event name.
    assert re.search(r'addEventListener\("achievement\.unlocked"', html)


def test_dashboard_handles_level_up_sse(html: str) -> None:
    assert "level_up" in html
    assert re.search(r'addEventListener\("profile\.level_up"', html)


def test_dashboard_default_llm_picker_present(html: str) -> None:
    assert 'id="defaultLlm"' in html
    # Persist via localStorage.
    assert "vibedump.defaultLlm" in html


def test_dashboard_xp_bar_present(html: str) -> None:
    assert 'id="xpBar"' in html
    assert 'id="xpFill"' in html


def test_dashboard_achievements_list_present(html: str) -> None:
    assert 'id="achList"' in html


def test_dashboard_profile_section_in_order(html: str) -> None:
    # Sections in the settings drawer must appear in this order: Profile,
    # Achievements, Default LLM, New Dump, Providers.
    pos = {
        "profile": html.find("<h2>Profile</h2>"),
        "achievements": html.find("<h2>Achievements</h2>"),
        "default_llm": html.find("<h2>Default LLM</h2>"),
        "new_dump": html.find("<h2>New Dump</h2>"),
        "providers": html.find("<h2>Providers</h2>"),
    }
    assert all(p > 0 for p in pos.values()), f"missing section(s): {pos}"
    order = sorted(pos.items(), key=lambda kv: kv[1])
    assert [k for k, _ in order] == ["profile", "achievements", "default_llm", "new_dump", "providers"], \
        f"sections out of order: {order}"


def test_dashboard_balanced_tag_count(html: str) -> None:
    # Cheap smoke check: opening vs closing counts for the major elements
    # we added should be balanced.
    for tag in ("html", "head", "body", "aside", "section", "form", "script", "style"):
        opens = len(re.findall(rf"<{tag}\b", html))
        closes = len(re.findall(rf"</{tag}>", html))
        assert opens == closes, f"unbalanced <{tag}>: {opens} open vs {closes} close"


def test_dashboard_no_obvious_js_syntax_smoke(html: str) -> None:
    # Extract the <script> body and check braces/brackets balance. Catches
    # the kind of edit that drops a closing brace.
    m = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert m, "no <script> block found"
    js = m.group(1)
    assert js.count("{") == js.count("}"), "unbalanced JS braces"
    assert js.count("(") == js.count(")"), "unbalanced JS parens"
    assert js.count("[") == js.count("]"), "unbalanced JS brackets"
