"""Regression checks for the M5 dashboard markup.

After the UI-00 refactor the dashboard is split into three files:
  - dashboard.html  – minimal HTML shell (links css/js externally)
  - dashboard.css   – all styles
  - dashboard.js    – all JavaScript

Tests that previously checked inline ``<style>`` / ``<script>`` content
now check the relevant external file instead. The ``html`` fixture still
covers the HTML shell for structural / element checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "vibedump" / "static"
DASHBOARD_PATH = STATIC_DIR / "dashboard.html"
DASHBOARD_CSS_PATH = STATIC_DIR / "dashboard.css"
DASHBOARD_JS_PATH = STATIC_DIR / "dashboard.js"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def html() -> str:
    assert DASHBOARD_PATH.exists(), f"dashboard.html missing at {DASHBOARD_PATH}"
    text = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert text, "dashboard.html is empty"
    return text


@pytest.fixture(scope="module")
def js() -> str:
    assert DASHBOARD_JS_PATH.exists(), f"dashboard.js missing at {DASHBOARD_JS_PATH}"
    text = DASHBOARD_JS_PATH.read_text(encoding="utf-8")
    assert text, "dashboard.js is empty"
    return text


@pytest.fixture(scope="module")
def css() -> str:
    assert DASHBOARD_CSS_PATH.exists(), f"dashboard.css missing at {DASHBOARD_CSS_PATH}"
    text = DASHBOARD_CSS_PATH.read_text(encoding="utf-8")
    assert text, "dashboard.css is empty"
    return text


# ---------------------------------------------------------------------------
# UI-00: Shell structure — link tags for external css/js
# ---------------------------------------------------------------------------


def test_dashboard_links_external_css(html: str) -> None:
    assert re.search(r'<link\b[^>]*rel="stylesheet"[^>]*/static/dashboard\.css', html), \
        "dashboard.html must contain <link rel='stylesheet' …/static/dashboard.css>"


def test_dashboard_links_external_js(html: str) -> None:
    assert re.search(r'<script\b[^>]*src="/static/dashboard\.js"', html), \
        "dashboard.html must contain <script src='/static/dashboard.js'>"


def test_dashboard_no_inline_style_block(html: str) -> None:
    assert not re.search(r"<style\b", html), \
        "dashboard.html must not contain inline <style> blocks after UI-00 refactor"


def test_dashboard_no_inline_script_block(html: str) -> None:
    # Only an external <script src="..."> tag is allowed; no inline JS.
    inline = re.findall(r"<script(?:\s[^>]*)?>(.+?)</script>", html, re.DOTALL)
    assert not inline, f"dashboard.html has unexpected inline <script> content: {inline[:1]}"


# ---------------------------------------------------------------------------
# HTML shell: preserved structural / element checks
# ---------------------------------------------------------------------------


def test_dashboard_file_is_non_empty_and_readable(html: str) -> None:
    assert len(html) > 1000, "dashboard.html shrank unexpectedly"


def test_dashboard_has_meta_and_viewport(html: str) -> None:
    assert re.search(r'<meta\s+charset="utf-8"', html, re.IGNORECASE)
    assert re.search(r'<meta\s+name="viewport"', html, re.IGNORECASE)


def test_dashboard_mascot_uses_img_tag(html: str) -> None:
    assert re.search(r'<img\s+id="mascotImg"', html), "mascot <img> element missing"
    m = re.search(r'<div\s+class="mascot"[^>]*id="mascot"[^>]*>(.*?)</div>', html, re.DOTALL)
    assert m, "mascot parent div not found"
    inner = m.group(1)
    assert "<img" in inner, "mascot parent must contain an <img>"
    assert "class=\"body\"" not in inner, "stale .body element still present in mascot"
    assert "class=\"eye" not in inner, "stale .eye element still present in mascot"
    assert "class=\"mouth\"" not in inner, "stale .mouth element still present in mascot"


def test_dashboard_xp_bar_present(html: str) -> None:
    assert 'id="xpBar"' in html
    assert 'id="xpFill"' in html


def test_dashboard_achievements_list_present(html: str) -> None:
    assert 'id="achList"' in html


def test_dashboard_default_llm_picker_in_html(html: str) -> None:
    assert 'id="defaultLlm"' in html


def test_dashboard_default_llm_localstorage_in_js(js: str) -> None:
    assert "vibedump.defaultLlm" in js


def test_dashboard_profile_section_in_order(html: str) -> None:
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
    # script/style are intentionally absent from the inline check list since
    # the refactored shell only contains an external <script src="…"> tag.
    for tag in ("html", "head", "body", "aside", "section", "form"):
        opens = len(re.findall(rf"<{tag}\b", html))
        closes = len(re.findall(rf"</{tag}>", html))
        assert opens == closes, f"unbalanced <{tag}>: {opens} open vs {closes} close"


# ---------------------------------------------------------------------------
# P0-3: Focus-trap attributes on drawers (role=dialog, aria-modal)
# ---------------------------------------------------------------------------


def test_dashboard_drawers_have_dialog_role(html: str) -> None:
    assert re.search(r'id="drawer"[^>]*role="dialog"', html) or \
           re.search(r'role="dialog"[^>]*id="drawer"', html), \
        "#drawer must have role='dialog'"
    assert re.search(r'id="settingsDrawer"[^>]*role="dialog"', html) or \
           re.search(r'role="dialog"[^>]*id="settingsDrawer"', html), \
        "#settingsDrawer must have role='dialog'"


def test_dashboard_drawers_have_aria_modal(html: str) -> None:
    assert html.count('aria-modal="true"') >= 2, \
        "Both drawers must have aria-modal='true'"


# ---------------------------------------------------------------------------
# P1-6: SSE indicator element in HTML
# ---------------------------------------------------------------------------


def test_dashboard_sse_indicator_present(html: str) -> None:
    assert 'id="sseIndicator"' in html, "SSE indicator element missing"
    assert "sse-dot" in html, ".sse-dot class missing from indicator"


# ---------------------------------------------------------------------------
# P1-15: Battery widget element in HTML
# ---------------------------------------------------------------------------


def test_dashboard_battery_widget_present(html: str) -> None:
    assert 'id="batteryWidget"' in html, "battery widget element missing"


# ---------------------------------------------------------------------------
# JS file: API endpoints and SSE event handlers (previously in inline script)
# ---------------------------------------------------------------------------


def test_dashboard_references_achievements_endpoint(js: str) -> None:
    assert "/api/achievements" in js


def test_dashboard_references_profile_endpoint(js: str) -> None:
    assert "/api/profile" in js
    assert 'method: "PATCH"' in js


def test_dashboard_handles_achievement_unlocked_sse(js: str) -> None:
    assert "achievement.unlocked" in js
    assert re.search(r'addEventListener\("achievement\.unlocked"', js)


def test_dashboard_handles_level_up_sse(js: str) -> None:
    assert "level_up" in js
    assert re.search(r'addEventListener\("profile\.level_up"', js)


def test_dashboard_no_obvious_js_syntax_smoke(js: str) -> None:
    assert js.count("{") == js.count("}"), "unbalanced JS braces in dashboard.js"
    assert js.count("(") == js.count(")"), "unbalanced JS parens in dashboard.js"
    assert js.count("[") == js.count("]"), "unbalanced JS brackets in dashboard.js"


# ---------------------------------------------------------------------------
# P0-1: Markdown renderer present in dashboard.js
# ---------------------------------------------------------------------------


def test_dashboard_markdown_renderer_present(js: str) -> None:
    assert "renderMarkdown" in js, "renderMarkdown function missing from dashboard.js"
    assert "inlineFormat" in js, "inlineFormat helper missing from dashboard.js"
    assert "escHtml" in js, "escHtml helper missing from dashboard.js"


# ---------------------------------------------------------------------------
# P0-4: Empty state helper present in dashboard.js
# ---------------------------------------------------------------------------


def test_dashboard_empty_state_function_present(js: str) -> None:
    assert "createEmptyState" in js, "createEmptyState function missing from dashboard.js"
    assert "/api/mascot/idle.png" in js, "Dumpi idle.png reference missing from empty-state helper"


# ---------------------------------------------------------------------------
# P0-5: Error toast level support in dashboard.js and CSS
# ---------------------------------------------------------------------------


def test_dashboard_error_toast_level_in_js(js: str) -> None:
    assert re.search(r'showToast\s*\(', js), "showToast function missing"
    assert "toast--error" in js, "toast--error class not applied in showToast"


def test_dashboard_error_toast_css_class(css: str) -> None:
    assert ".toast--error" in css, ".toast--error CSS class missing"
    assert ".toast-close" in css, ".toast-close CSS class missing"


# ---------------------------------------------------------------------------
# P1-6: SSE indicator JS logic
# ---------------------------------------------------------------------------


def test_dashboard_sse_status_function_in_js(js: str) -> None:
    assert "setSSEStatus" in js, "setSSEStatus function missing from dashboard.js"
    assert re.search(r'setSSEStatus\("connecting"\)', js), \
        "SSE indicator not set to 'connecting' on connect"


# ---------------------------------------------------------------------------
# P1-15: Battery load function in dashboard.js
# ---------------------------------------------------------------------------


def test_dashboard_battery_function_in_js(js: str) -> None:
    assert "loadBattery" in js, "loadBattery function missing from dashboard.js"
    assert "hardwarePisugar" in js, "pisugar API reference missing from dashboard.js"


# ---------------------------------------------------------------------------
# P1-2: Typewriter animation in dashboard.js
# ---------------------------------------------------------------------------


def test_dashboard_typewriter_function_in_js(js: str) -> None:
    assert "typewriterText" in js, "typewriterText function missing from dashboard.js"
    assert "_typewriterTimer" in js, "_typewriterTimer variable missing from dashboard.js"


# ---------------------------------------------------------------------------
# CSS: new feature styles present
# ---------------------------------------------------------------------------


def test_dashboard_sse_dot_css(css: str) -> None:
    assert ".sse-dot" in css, ".sse-dot CSS rule missing"


def test_dashboard_battery_widget_css(css: str) -> None:
    assert ".battery-widget" in css, ".battery-widget CSS rule missing"


def test_dashboard_empty_state_css(css: str) -> None:
    assert ".empty-state" in css, ".empty-state CSS rule missing"
    assert ".empty-state-img" in css, ".empty-state-img CSS rule missing"


def test_dashboard_md_body_css(css: str) -> None:
    assert ".md-body" in css, ".md-body CSS rule missing"
