"""The worker's task instruction for one artifact.

The router brain never writes a page itself: it hands a short request to the
mission stack, and a worker on the strongest model the install has writes the
whole file. This module turns that request into the instruction the worker
reads — the one text that decides whether what comes back is a real artifact
(one self-contained page that renders inside the app's sandbox) or a project
scaffold with a README.

Everything a finished artifact must be is stated HERE, because the worker
shares none of the app's context. The rules are the sandbox's rules: the page
is framed with scripts allowed but every network request blocked, so an
external font, CDN script or remote image is not "slightly slower" — it is a
broken page. Stated once, in the brief, is the only place that can prevent it.

Pure function of its inputs: no clock, no filesystem, no randomness. What the
worker receives for a given request is byte-identical across runs, which is
what lets a golden test pin the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from jarvis.visuals.brand import BRAND

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
    '"content follows" shell is a FAILURE — never ship one. If a detail is '
    "unspecified, pick a rich, sensible default and build the finished page."
)

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
) -> ArtifactBrief:
    """Compose the worker instruction for one artifact.

    Inputs:
        request: what the user wants on the page — the content, the data,
            the style wishes — in the user's own words or the brain's faithful
            summary of them. Never empty; the caller validates.
        title: the artifact's title; names the file and the ``<title>``.
        language: the turn's output language code (de / en / es). The page's
            visible text is written in it.
        previous_html: for a revision, the page to start from (the worker
            rewrites the whole file with the change applied).
        previous_filename: the previous page's filename, kept on a revision so
            the new version lands under the same name.
    """
    clean_title = " ".join((title or "").split()) or "Artifact"
    clean_request = (request or "").strip()
    revision = bool(previous_html)
    filename = (
        previous_filename if revision and previous_filename else artifact_filename(clean_title)
    )
    lang_name = _language_name(language)
    lang_code = (language or "").strip().lower() or "en"
    b = BRAND

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
            ]
        ),
        "\n".join(
            [
                "## Design",
                "- Dark brand look by default: page background "
                f"{b['bg']}, cards {b['bg_card']}, text {b['text']}, muted text "
                f"{b['text_muted']}, accent {b['primary']}, borders {b['border']}. "
                "Add an `@media (prefers-color-scheme: light)` block with a "
                "matching light palette (paper background, near-black text, the "
                "same accent) so the page reads well in both modes.",
                "- Calm typography on a system-ui font stack, generous spacing, "
                "12–14 px rounded cards, 1 px borders, no gratuitous animation.",
                "- Responsive: readable from 360 px to 1600 px; wide tables, code "
                "and diagrams scroll inside their own container, never the page "
                "sideways.",
                "- Real content, never filler: put the numbers, facts and data the "
                "request names into the page. When something needed is missing, "
                "make a clearly labelled assumption — never lorem ipsum, never "
                "'TODO'.",
            ]
        ),
        "\n".join(
            [
                "## Done means",
                "- The file opens from disk, with no network, and renders "
                "correctly with no console errors.",
                "- The page shows what was asked for — the title, the content, the "
                "data — and says nothing about how it was built.",
                "- Nothing else was created or changed.",
            ]
        ),
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
    "MAX_PREVIOUS_HTML_CHARS",
    "ArtifactBrief",
    "artifact_filename",
    "build_artifact_brief",
]
