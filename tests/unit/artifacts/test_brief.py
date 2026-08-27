"""The artifact brief — what a worker is told, pinned.

The brief is the one text that decides whether a mission returns an artifact
(one self-contained page that renders in the app's sandbox) or a project
scaffold. So what is tested is the contract, not the prose: the file rule, the
no-network rule, the language, the brand palette, the quality lead the Outputs
rail strips, and the revision block that carries the previous page.
"""

from __future__ import annotations

import re

from jarvis.artifacts.brief import (
    MAX_PREVIOUS_HTML_CHARS,
    artifact_filename,
    build_artifact_brief,
)
from jarvis.artifacts.design_guide import THEME_BOOTSTRAP_JS, THEME_CSS
from jarvis.missions.stream_evidence import clean_request_body


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
    # The app's own design system rides along, verbatim, so the page matches it.
    assert THEME_CSS in prompt and THEME_BOOTSTRAP_JS in prompt
    assert "## Read the request first" in prompt
    assert "## Charts and diagrams" in prompt
    assert "## What reads as generated" in prompt
    assert "## Done means" in prompt


def test_theme_tokens_are_defined_in_every_scope() -> None:
    """The classic unreadable-artifact bug is a token that exists in one theme
    only. Every `--name` in the dark root block must also exist in the OS-light
    block and in the explicit light stamp."""
    blocks = re.findall(r"\{([^{}]*?--[^{}]*)\}", THEME_CSS)
    scoped = [b for b in blocks if "--bg:" in b]
    assert len(scoped) == 3, "dark root, OS-light, explicit-light"
    names = [set(re.findall(r"(--[a-z0-9-]+):", b)) for b in scoped]
    assert names[0] >= names[1] and names[1] == names[2]
    # The chart palettes documented as validated are the ones shipped.
    dark = "--s1:#C98500;--s2:#4F8EF7;--s3:#E0633F;--s4:#1F9E7F;--s5:#9085E9;--s6:#C84E8A"
    light = "--s1:#A86B00;--s2:#2A6FD0;--s3:#D4532E;--s4:#158F6B;--s5:#5B46C2;--s6:#C23F86"
    assert dark in THEME_CSS and light in THEME_CSS


def test_the_brief_names_the_forms_the_user_can_ask_for() -> None:
    prompt = build_artifact_brief("x", title="T", language="en").prompt
    for phrase in ("Balken", "Vergleich", "erklär mir", "dashboard", "timeline", "landing page"):
        assert phrase in prompt, phrase
    # And the things our own archive got wrong are named as forbidden.
    for phrase in ("gradient", "Tailwind rainbow", "emoji", "dual axes", "Web fonts"):
        assert phrase in prompt, phrase


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
    # The guide itself is ~12k chars; the clipped page adds the cap, not more.
    assert len(brief.prompt) < MAX_PREVIOUS_HTML_CHARS + 20_000


def test_brief_is_deterministic() -> None:
    a = build_artifact_brief("Same.", title="Same", language="es")
    b = build_artifact_brief("Same.", title="Same", language="es")
    assert a == b


def test_facts_rule_forbids_invented_personal_data_and_defaults_only_design() -> None:
    """The forensic of 2026-08-27: a briefing built with no data came back
    full of invented senders and meetings. The lead now defaults DESIGN only,
    and the facts rule names the honest empty section as the correct page."""
    prompt = build_artifact_brief("A morning briefing.", title="Briefing", language="en").prompt
    lead = prompt.split("\n\n", 1)[0]
    assert "production-quality" in lead  # the rail still strips it by this phrase
    assert "detail of the DESIGN is unspecified" in lead
    assert "A FACT is never defaulted" in lead
    assert "## Facts, never inventions" in prompt
    assert "Never invent personal data" in prompt
    assert "no inbox data was available to this build" in prompt
    assert "honest and half-empty is CORRECT" in prompt
    assert "labelled as sample data" in prompt
    # The rule sits with the build rules, before the design guide.
    assert prompt.index("## Facts, never inventions") < prompt.index("## Read the request first")


def test_source_data_rides_between_the_facts_rule_and_the_design_guide() -> None:
    section = (
        "## Source data — the only facts about the user's own data\n"
        "### Gmail inbox — available"
    )
    with_data = build_artifact_brief(
        "My mail as a page.", title="Mail", language="en", source_data=section
    ).prompt
    assert section in with_data
    assert (
        with_data.index("## Facts, never inventions")
        < with_data.index(section)
        < with_data.index("## Read the request first")
    )
    # Empty / whitespace source data adds nothing — the brief stays byte-identical.
    plain = build_artifact_brief("My mail as a page.", title="Mail", language="en").prompt
    assert build_artifact_brief(
        "My mail as a page.", title="Mail", language="en", source_data="  \n"
    ).prompt == plain
    assert "## Source data" not in plain
