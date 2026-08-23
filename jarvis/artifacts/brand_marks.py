"""Original brand marks for the pages a worker writes.

A generated page that names OpenAI, Anthropic, Google or Slack reaches for a
logo — and without help a model draws the one thing the maintainer will not
accept: a coloured tile with a letter in it ("G", "OA", "A"), an emoji, or an
invented glyph (the archive of 2026-08-23 had all three). The app already
bundles the ORIGINAL marks for the provider and plugin screens, with a
provenance ledger per folder (``LOGOS.md``). This module hands the marks a
request mentions to the worker, inline, so the page carries the real mark or
— when there is none — plain text, never a stand-in.

The worker cannot read the repo (an artifact task runs in a lean workspace,
and an installed app has no source checkout at all), so the SVG rides along in
the brief. Only the marks the request names are included, in order of first
mention, under a size cap — a request that names nothing adds one short rule
and nothing else.

Render paths follow the ledgers: a ``colour`` mark is pasted untouched (the
colours ARE the brand); a ``mono`` mark — a glyph whose own brand is
black-on-white or white-on-black, or a vendor's light variant bundled for the
dark app — is normalised to ``currentColor`` so it follows the page's ink on
both themes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from jarvis.core.paths import repo_root

log = logging.getLogger(__name__)

Render = Literal["colour", "mono"]

# Where the app keeps its marks. Both folders carry a LOGOS.md ledger that
# records source and legal basis per file; the CI gate
# (scripts/ci/check_brand_logos.py) holds them to it.
_ASSETS_SUBDIR: Final = Path("jarvis") / "ui" / "web" / "frontend" / "src" / "assets"

# How many marks one brief carries, and how much text they may add. A request
# that names a dozen companies is a comparison table, not a logo wall.
MAX_MARKS: Final = 8
MAX_TOTAL_SVG_CHARS: Final = 24_000


@dataclass(frozen=True)
class BrandFamily:
    """One bundled mark and the names in a request that call for it."""

    family: str
    label: str
    folder: str
    file: str
    render: Render
    aliases: tuple[str, ...]


# Order matters twice: a longer alias must win over a shorter one it contains
# ("Google Cloud" before any bare "Google"), and the table's order is the
# tie-break when two families first appear at the same position.
BRAND_FAMILIES: Final[tuple[BrandFamily, ...]] = (
    BrandFamily(
        "google-cloud",
        "Google Cloud / Vertex AI",
        "providers",
        "google-cloud.svg",
        "colour",
        ("Google Cloud", "Vertex AI", "Vertex"),
    ),
    BrandFamily(
        "gemini",
        "Gemini",
        "providers",
        "gemini.svg",
        "colour",
        ("Gemini", "Google DeepMind", "DeepMind"),
    ),
    BrandFamily(
        "openai",
        "OpenAI",
        "providers",
        "openai.svg",
        "mono",
        ("OpenAI", "ChatGPT", "GPT-5", "GPT-4", "GPT", "Codex", "Whisper", "o3", "o4"),
    ),
    BrandFamily(
        "claude", "Claude / Anthropic", "providers", "claude.svg", "colour", ("Anthropic", "Claude")
    ),
    BrandFamily("xai", "xAI / Grok", "providers", "xai.svg", "mono", ("xAI", "Grok")),
    BrandFamily("nvidia", "NVIDIA", "providers", "nvidia.svg", "colour", ("NVIDIA", "Nvidia")),
    BrandFamily("ollama", "Ollama", "providers", "ollama.svg", "mono", ("Ollama",)),
    BrandFamily("groq", "Groq", "providers", "groq.svg", "mono", ("Groq",)),
    BrandFamily("openrouter", "OpenRouter", "providers", "openrouter.svg", "mono", ("OpenRouter",)),
    BrandFamily(
        "elevenlabs",
        "ElevenLabs",
        "providers",
        "elevenlabs.svg",
        "mono",
        ("ElevenLabs", "Eleven Labs"),
    ),
    BrandFamily("cartesia", "Cartesia", "providers", "cartesia.svg", "colour", ("Cartesia",)),
    BrandFamily("github", "GitHub", "brands", "github.svg", "mono", ("GitHub",)),
    BrandFamily("vercel", "Vercel", "brands", "vercel.svg", "mono", ("Vercel",)),
    BrandFamily("notion", "Notion", "brands", "notion.svg", "mono", ("Notion",)),
    BrandFamily("slack", "Slack", "brands", "slack.svg", "colour", ("Slack",)),
    BrandFamily("discord", "Discord", "brands", "discord.svg", "colour", ("Discord",)),
    BrandFamily("telegram", "Telegram", "brands", "telegram.svg", "colour", ("Telegram",)),
    BrandFamily("spotify", "Spotify", "brands", "spotify.svg", "colour", ("Spotify",)),
    BrandFamily(
        "youtube_music",
        "YouTube Music",
        "brands",
        "youtube_music.svg",
        "colour",
        ("YouTube Music",),
    ),
    BrandFamily("gmail", "Gmail", "brands", "gmail.svg", "colour", ("Gmail",)),
    BrandFamily(
        "google_calendar",
        "Google Calendar",
        "brands",
        "google_calendar.svg",
        "colour",
        ("Google Calendar",),
    ),
    BrandFamily(
        "google_drive", "Google Drive", "brands", "google_drive.svg", "colour", ("Google Drive",)
    ),
    BrandFamily("dropbox", "Dropbox", "brands", "dropbox.svg", "colour", ("Dropbox",)),
    BrandFamily("linear", "Linear", "brands", "linear.svg", "colour", ("Linear",)),
    BrandFamily("asana", "Asana", "brands", "asana.svg", "colour", ("Asana",)),
    BrandFamily("airtable", "Airtable", "brands", "airtable.svg", "colour", ("Airtable",)),
    BrandFamily("todoist", "Todoist", "brands", "todoist.svg", "colour", ("Todoist",)),
    BrandFamily("clickup", "ClickUp", "brands", "clickup.svg", "colour", ("ClickUp",)),
    BrandFamily("supabase", "Supabase", "brands", "supabase.svg", "colour", ("Supabase",)),
    BrandFamily("canva", "Canva", "brands", "canva.svg", "colour", ("Canva",)),
    BrandFamily(
        "home_assistant",
        "Home Assistant",
        "brands",
        "home_assistant.svg",
        "colour",
        ("Home Assistant",),
    ),
    BrandFamily("cal_com", "Cal.com", "brands", "cal_com.svg", "mono", ("Cal.com",)),
)


@dataclass(frozen=True)
class BrandMark:
    """One mark, ready to paste into a page."""

    family: str
    label: str
    render: Render
    svg: str


_ROOT_ATTR_RE: Final = re.compile(
    r"""\s(?:width|height|style|class|id)\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE
)
_TITLE_RE: Final = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_XML_DECL_RE: Final = re.compile(r"<\?xml[^>]*\?>\s*", re.IGNORECASE)
_COMMENT_RE: Final = re.compile(r"<!--.*?-->", re.DOTALL)
_INK_FILL_RE: Final = re.compile(
    r"""fill\s*=\s*["'](?:#f{3,8}|#0{3,8}|white|black)["']""", re.IGNORECASE
)
_INK_STYLE_FILL_RE: Final = re.compile(
    r"""fill\s*:\s*(?:#f{3,8}|#0{3,8}|white|black)\b""", re.IGNORECASE
)
_SVG_OPEN_RE: Final = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


def _normalise_svg(raw: str, render: Render) -> str:
    """One clean inline ``<svg>``: no XML prolog, comments or root sizing; a
    mono mark on ``currentColor`` so it follows the page's ink."""
    text = _XML_DECL_RE.sub("", raw)
    text = _COMMENT_RE.sub("", text)
    text = _TITLE_RE.sub("", text)
    match = _SVG_OPEN_RE.search(text)
    if match is None:
        return ""
    open_tag = _ROOT_ATTR_RE.sub("", match.group(0))
    if render == "mono":
        # Ink fills anywhere in the mark become the page's ink; a root with no
        # fill gets currentColor so paths that inherit follow it too.
        text = _INK_FILL_RE.sub('fill="currentColor"', text)
        text = _INK_STYLE_FILL_RE.sub("fill:currentColor", text)
        open_tag = _INK_FILL_RE.sub('fill="currentColor"', open_tag)
        if "fill=" not in open_tag:
            open_tag = open_tag[:-1] + ' fill="currentColor">'
    text = text[: match.start()] + open_tag + text[match.end() :]
    return " ".join(text.split())


def assets_root() -> Path:
    """The folder the two mark ledgers live in (may not exist on an install)."""
    return repo_root() / _ASSETS_SUBDIR


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Word-bounded, case-insensitive; "GPT" must not match "GPTQ" or "egpt",
    # and "o3" must not match inside "foo3". Dots and hyphens in the alias are
    # literal.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def mentioned_families(text: str) -> list[BrandFamily]:
    """The families a request names, in order of first mention."""
    hits: list[tuple[int, int, BrandFamily]] = []
    for order, fam in enumerate(BRAND_FAMILIES):
        first = None
        for alias in fam.aliases:
            m = _alias_pattern(alias).search(text)
            if m and (first is None or m.start() < first):
                first = m.start()
        if first is not None:
            hits.append((first, order, fam))
    hits.sort()
    return [fam for _, _, fam in hits]


def find_brand_marks(
    text: str,
    *,
    root: Path | None = None,
    limit: int = MAX_MARKS,
    max_total_chars: int = MAX_TOTAL_SVG_CHARS,
) -> list[BrandMark]:
    """The original marks for the brands ``text`` names, ready to inline.

    Reads the bundled SVGs under ``root`` (default: the app's asset folder).
    A missing folder or file simply yields fewer marks — the brief's rule then
    tells the worker to write the name as text — never an error.
    """
    base = root if root is not None else assets_root()
    marks: list[BrandMark] = []
    budget = max_total_chars
    for fam in mentioned_families(text):
        if len(marks) >= limit:
            break
        path = base / fam.folder / fam.file
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:  # no bundled file (wheel without assets) = no mark; the rule covers it
            log.debug(
                "brand mark %s unavailable at %s; the brief says text, no stand-in",
                fam.family,
                path,
            )
            continue
        svg = _normalise_svg(raw, fam.render)
        if not svg or len(svg) > budget:
            continue
        budget -= len(svg)
        marks.append(BrandMark(family=fam.family, label=fam.label, render=fam.render, svg=svg))
    return marks


BRAND_MARKS_RULE: Final = """\
## Brand marks — the original or none
When the page names a company or product and a mark belongs beside the name, use the \
ORIGINAL mark, inline: the `<svg>` blocks supplied below, pasted as they are (size them \
with CSS — 16–20px beside text, 24–32px on a tile; never stretch, never recolour a \
colour mark; a mono mark inherits `color`, so it follows the ink on both themes). If a \
brand has no supplied mark, write its name in text — set in the same type as its \
neighbours, optionally with a neutral dot or initial-free tile. NEVER stand a logo in: \
no lettered tile ("G", "OA", "A" in a coloured square), no emoji, no invented glyph, no \
generic "AI" spark. A mark identifies a vendor — it never implies endorsement and never \
appears where the text does not name the vendor."""


def brand_marks_section(marks: list[BrandMark]) -> str:
    """The brief section: the rule, then the marks the request called for."""
    if not marks:
        return BRAND_MARKS_RULE + "\nThis request names no brand with a bundled mark."
    lines = [
        BRAND_MARKS_RULE,
        "",
        "Supplied marks (paste verbatim, each once in a `<symbol>`/`<defs>` block or "
        "inline where used):",
    ]
    for mark in marks:
        how = "colour mark — do not recolour"
        if mark.render == "mono":
            how = "mono — inherits `color`"
        lines.append(f"- {mark.label} (`{mark.family}`, {how}):")
        lines.append("```html")
        lines.append(mark.svg)
        lines.append("```")
    return "\n".join(lines)


__all__ = [
    "BRAND_FAMILIES",
    "BRAND_MARKS_RULE",
    "MAX_MARKS",
    "MAX_TOTAL_SVG_CHARS",
    "BrandFamily",
    "BrandMark",
    "assets_root",
    "brand_marks_section",
    "find_brand_marks",
    "mentioned_families",
]
