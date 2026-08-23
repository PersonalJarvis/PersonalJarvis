"""The artifact brief — what a worker is told, pinned.

The brief is the one text that decides whether a mission returns an artifact
(one self-contained page that renders in the app's sandbox) or a project
scaffold. So what is tested is the contract, not the prose: the file rule, the
no-network rule, the language, the brand palette, the quality lead the Outputs
rail strips, and the revision block that carries the previous page.
"""

from __future__ import annotations

from jarvis.artifacts.brief import (
    MAX_PREVIOUS_HTML_CHARS,
    artifact_filename,
    build_artifact_brief,
)
from jarvis.missions.stream_evidence import clean_request_body
from jarvis.visuals.brand import BRAND


def test_filename_comes_from_the_title_and_stays_short() -> None:
    assert artifact_filename("Sales dashboard — Q3 2026") == "sales-dashboard-q3-2026.html"
    assert artifact_filename("   ") == "artifact.html"
    long = artifact_filename("x" * 200)
    assert long.endswith(".html") and len(long) <= 48 + len(".html")


def test_brief_states_the_single_file_and_no_network_contract() -> None:
    brief = build_artifact_brief(
        "Compare the three plans by price and features.",
        title="Plan comparison",
        language="en",
    )
    assert brief.filename == "plan-comparison.html"
    assert brief.revision is False
    prompt = brief.prompt
    assert "Exactly ONE self-contained HTML file named `plan-comparison.html`" in prompt
    assert "no CDN scripts, no web fonts" in prompt
    assert "JavaScript is allowed" in prompt
    assert "`<title>` that names the artifact: `Plan comparison`" in prompt
    assert "written in English" in prompt
    assert "Compare the three plans by price and features." in prompt
    # Brand palette rides along so the page matches the app.
    assert BRAND["primary"] in prompt and BRAND["bg"] in prompt
    assert "prefers-color-scheme: light" in prompt


def test_language_code_lands_in_the_html_lang_and_the_content_rule() -> None:
    prompt = build_artifact_brief("Zeig die Zahlen.", title="Zahlen", language="de").prompt
    assert '<html lang="de">' in prompt
    assert "written in German" in prompt


def test_outputs_rail_sees_the_artifact_line_not_the_quality_lead() -> None:
    """``clean_request_body`` strips the first paragraph by its phrasing — the
    brief keeps that phrasing so the rail shows "Artifact: <title>" first."""
    brief = build_artifact_brief("Build it.", title="Team roster", language="en")
    body = clean_request_body(brief.prompt)
    assert body.startswith("Artifact: Team roster\nBuild it.")
    assert "production-quality" not in body.split("\n\n", 1)[0]


def test_revision_carries_the_previous_page_and_keeps_its_filename() -> None:
    previous = "<!doctype html><html><head><title>Old</title></head><body>v1</body></html>"
    brief = build_artifact_brief(
        "Make the bars red.",
        title="Old",
        language="en",
        previous_html=previous,
        previous_filename="old-page.html",
    )
    assert brief.revision is True
    assert brief.filename == "old-page.html"
    assert "## Starting point" in brief.prompt
    assert previous in brief.prompt
    assert "Rewrite the ENTIRE file `old-page.html`" in brief.prompt


def test_an_oversized_previous_page_is_clipped_head_and_tail() -> None:
    huge = "A" * (MAX_PREVIOUS_HTML_CHARS + 5_000) + "ZEND"
    brief = build_artifact_brief("Tweak.", title="Huge", language="en", previous_html=huge)
    assert "middle of the previous version omitted" in brief.prompt
    assert brief.prompt.rstrip("`\n").endswith("ZEND")
    assert len(brief.prompt) < MAX_PREVIOUS_HTML_CHARS + 6_000


def test_brief_is_deterministic() -> None:
    a = build_artifact_brief("Same.", title="Same", language="es")
    b = build_artifact_brief("Same.", title="Same", language="es")
    assert a == b
