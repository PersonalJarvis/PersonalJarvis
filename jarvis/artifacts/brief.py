"""The worker's task instruction for one artifact.

The router brain never writes a page itself: it hands a short request to the
mission stack, and a worker on the strongest model the install has writes the
whole file. This module turns that request into the instruction the worker
reads — the one text that decides whether what comes back is a real artifact
(one self-contained page that renders inside the app's sandbox) or a project
scaffold with a README.

Everything a finished artifact must be is stated HERE, because the worker
shares none of the app's context. Two kinds of rule ride together:

* the sandbox's rules — the page is framed with scripts allowed but every
  network request blocked, so an external font, CDN script or remote image is
  not "slightly slower", it is a broken page;
* the design standard (:mod:`jarvis.artifacts.design_guide`) — the app's own
  tokens to paste, how to read the request so the form follows the ask, the
  chart method, and the explicit list of what reads as generated. Without it a
  worker reaches for the defaults every model reaches for, and the result is
  the gradient-hero page our archive was full of (2026-08-23).

Pure function of its inputs: no clock, no filesystem, no randomness. What the
worker receives for a given request is byte-identical across runs, which is
what lets a golden test pin the contract.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from jarvis.artifacts.brand_marks import BrandMark, brand_marks_section
from jarvis.artifacts.design_guide import (
    AVOID,
    CHARTS,
    DESIGN_SYSTEM,
    DONE_MEANS,
    READ_THE_REQUEST,
    THEME_BOOTSTRAP_JS,
    THEME_CSS,
)

# How much of a previous version rides along on a revision. The worker rewrites
# the whole file from it, so the cap is generous — but a page that outgrew it
# is summarised by its head and tail rather than blowing the prompt past what a
# worker's context can take.
MAX_PREVIOUS_HTML_CHARS: Final = 160_000

# Title → filename. Kept short: the archive path is already deep
# (run / tasks / id / artifacts / files / name) and Windows callers still meet
# MAX_PATH.
_MAX_FILENAME_STEM: Final = 48
_NON_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")

# The first paragraph of every mission prompt is the standing quality directive
# ``spawn_worker`` also leads with. It is recognised by its phrasing
# ("production-quality") in ``jarvis.missions.stream_evidence.clean_request_body``,
# which strips it so the Outputs rail shows the real ask — so the wording below
# must keep that phrase, and the artifact line must come right after it.
_QUALITY_LEAD: Final = (
    "Deliver a complete, polished, production-quality artifact that fully "
    "satisfies the request below. A skeleton, stub, placeholder or "
    '"content follows" shell is a FAILURE — never ship one. If a detail of the '
    "DESIGN is unspecified — layout, order, colour, the wording of a label — "
    "pick a rich, sensible default and build the finished page. A FACT is "
    "never defaulted: see 'Facts, never inventions' below."
)

# The rule the forensic of 2026-08-27 was missing (mission 01a0426e-8d79: a
# "morning briefing from my calendar and mail" built by a worker with no
# data access came back full of invented senders, subjects and meetings —
# the old quality lead's "pick a rich default" read as permission). A
# worker sees no account of the user's; what it may state about that user
# is exactly what the brief carries, and an honest empty section beats a
# plausible fiction every time.
FACTS_RULE: Final = """\
## Facts, never inventions
The page is a factual document about THIS user. Every name, sender, subject, \
appointment, contact, amount, date, quote and number on it comes from the request \
or from the "Source data" section — nothing else.
- Never invent personal data: no made-up emails, senders, meetings, colleagues, \
companies, deadlines, balances or figures — not as an "example", not to fill a \
column, not to make the page look complete.
- Where the request asks for the user's own data (inbox, calendar, contacts, \
files, spending, messages …) and the Source data section carries nothing for it \
— missing, "nothing there", or "UNAVAILABLE" — the page says exactly that in \
that section, in one plain sentence (e.g. "Gmail is not connected — no inbox \
data was available to this build"), and leaves the section otherwise empty. A \
page that is honest and half-empty is CORRECT; a page full of plausible fiction \
is a FAILURE.
- Sample data is allowed only when the request itself asks for a sample, demo, \
mock-up or template — and then every sample element is visibly labelled as \
sample data on the page itself."""

_LANGUAGE_NAMES: Final[dict[str, str]] = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
}


@dataclass(frozen=True)
class ArtifactBrief:
    """The mission prompt plus the facts the tool reports back."""

    prompt: str
    """The complete worker instruction — what ``MissionManager.dispatch`` gets."""
    filename: str
    """The one file the worker is told to write, e.g. ``sales-dashboard.html``."""
    title: str
    """The artifact's title as it will appear in the page and the rail."""
    revision: bool
    """True when the brief starts from an existing page."""


def artifact_filename(title: str) -> str:
    """``"Sales dashboard — Q3"`` → ``"sales-dashboard-q3.html"``."""
    stem = _NON_SLUG_RE.sub("-", (title or "").lower()).strip("-")
    if len(stem) > _MAX_FILENAME_STEM:
        stem = stem[:_MAX_FILENAME_STEM].rstrip("-")
    return f"{stem or 'artifact'}.html"


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get((code or "").strip().lower(), "the language of the request")


def _bounded_previous(html: str) -> str:
    """The previous page, whole when it fits, head + tail when it does not."""
    text = html or ""
    if len(text) <= MAX_PREVIOUS_HTML_CHARS:
        return text
    half = MAX_PREVIOUS_HTML_CHARS // 2
    return (
        text[:half]
        + "\n\n<!-- … middle of the previous version omitted for length … -->\n\n"
        + text[-half:]
    )


def build_artifact_brief(
    request: str,
    *,
    title: str,
    language: str,
    previous_html: str | None = None,
    previous_filename: str | None = None,
    brand_marks: Sequence[BrandMark] = (),
    source_data: str | None = None,
) -> ArtifactBrief:
    """Compose the worker instruction for one artifact.

    Inputs:
        request: what the user wants on the page — the content, the data,
            the style wishes — in the user's own words or the brain's faithful
            summary of them. Never empty; the caller validates.
        source_data: the rendered ``## Source data`` section
            (:func:`jarvis.artifacts.source_data.render_source_data`) — the
            facts Jarvis read from the user's accounts for a page about their
            own mail or calendar, or the honest "unavailable" notes when it
            could not. Empty/None when the request names no such data. The
            caller fetches; this function stays free of I/O.
        title: the artifact's title; names the file and the ``<title>``.
        language: the turn's output language code (de / en / es). The page's
            visible text is written in it.
        previous_html: for a revision, the page to start from (the worker
            rewrites the whole file with the change applied).
        previous_filename: the previous page's filename, kept on a revision so
            the new version lands under the same name.
        brand_marks: the original marks for the brands the request names
            (:func:`jarvis.artifacts.brand_marks.find_brand_marks`), inlined
            so the page never draws a lettered tile in a logo's place. The
            caller resolves them; this function stays free of the filesystem.
    """
    clean_title = " ".join((title or "").split()) or "Artifact"
    clean_request = (request or "").strip()
    revision = bool(previous_html)
    filename = (
        previous_filename if revision and previous_filename else artifact_filename(clean_title)
    )
    lang_name = _language_name(language)
    lang_code = (language or "").strip().lower() or "en"

    sections: list[str] = [
        _QUALITY_LEAD,
        f"Artifact: {clean_title}\n{clean_request}",
        "\n".join(
            [
                "## What to build",
                f"Exactly ONE self-contained HTML file named `{filename}`, at the "
                "root of your working directory. That file IS the whole "
                "deliverable: no second file, no README, no project scaffold, no "
                "build step, no framework install.",
                '- Start with `<!doctype html>`, `<html lang="'
                + lang_code
                + '">`, `<meta charset="utf-8">`, a viewport meta tag and a '
                f"`<title>` that names the artifact: `{clean_title}`.",
                "- ALL CSS and JavaScript inline, inside the file. Nothing "
                "external: no CDN scripts, no web fonts, no remote images or "
                "stylesheets, no fetch/XHR/WebSocket. The page is shown inside a "
                "sandbox that blocks every network request, so one external "
                "reference means a broken page. Draw icons and illustrations as "
                "inline SVG; embed small images as data: URIs.",
                "- JavaScript is allowed and welcome for interactivity — tabs, "
                "filters, sorting, calculators, charts drawn on canvas or SVG. "
                "It must never call alert/confirm/prompt, never open windows, "
                "never navigate away, and must work with no network at all.",
                f"- The visible content is written in {lang_name} — every heading, "
                "label, caption and note. Code identifiers and comments stay English.",
                "- Responsive from 360 px to 1600 px; the page never scrolls sideways.",
                "- Real content, never filler: the numbers, facts and data the request "
                "names go into the page; a missing GENERAL fact (a rate, a definition) "
                "becomes a clearly labelled assumption — never lorem ipsum, never "
                "'TODO'. A missing fact about the USER is never assumed: see "
                "'Facts, never inventions'.",
            ]
        ),
        FACTS_RULE,
        *([source_data.strip()] if source_data and source_data.strip() else []),
        READ_THE_REQUEST,
        DESIGN_SYSTEM,
        "\n".join(
            [
                "```html",
                "<style>",
                THEME_CSS,
                "</style>",
                "<script>",
                THEME_BOOTSTRAP_JS,
                "</script>",
                "```",
            ]
        ),
        CHARTS,
        brand_marks_section(list(brand_marks)),
        AVOID,
        DONE_MEANS,
    ]

    if revision:
        sections.append(
            "\n".join(
                [
                    "## Starting point",
                    "This is a revision of an existing artifact. Rewrite the ENTIRE "
                    f"file `{filename}` with the requested change applied. Keep "
                    "everything the request did not ask to change — content, "
                    "structure, styling — and keep the same filename. The "
                    "previous version follows verbatim:",
                    "",
                    "```html",
                    _bounded_previous(previous_html or ""),
                    "```",
                ]
            )
        )

    return ArtifactBrief(
        prompt="\n\n".join(sections),
        filename=filename,
        title=clean_title,
        revision=revision,
    )


__all__ = [
    "FACTS_RULE",
    "MAX_PREVIOUS_HTML_CHARS",
    "ArtifactBrief",
    "artifact_filename",
    "build_artifact_brief",
]
