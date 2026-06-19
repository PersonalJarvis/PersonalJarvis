"""Prompt builders for the wiki curator LLM (Phase B1, Instance D).

Two pure functions:

* ``build_system_prompt(schema_md, vault_summary)`` — concatenates the
  binding ``schema.md`` (verbatim, never paraphrased) with a compact
  description of the current vault state and the JSON output contract.
* ``build_user_prompt(source_label, source_content, top_slugs)`` — wraps
  the new source in a turn template and points the LLM at the slugs most
  likely to be touched (top-10 by keyword overlap).

The companion ``compute_vault_summary(vault, log_path)`` collects the
per-type page counts and the three most recent log entries in a shape
that ``build_system_prompt`` consumes. Disk reads are limited to
``log.md`` so the function stays cheap.

No LLM calls, no disk writes. The module is pure Python.

Owned by Instance D. See ``docs/phase-b1-wiki-curator/README.md`` Part 5.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Output-contract block appended to schema.md inside the system prompt.
# Keep this in lockstep with ``PageUpdate`` (protocols.py) and with the
# parser in ``curator_llm._parse_updates``.
_OUTPUT_CONTRACT = """\
## Salience filter (read first, binding)

The single most important decision you make is: "does this source carry
a fact worth persisting, or is it smalltalk?". Get this wrong in the
direction of false positives and the wiki fills with chitchat. Get it
wrong in the direction of false negatives and Personal Jarvis forgets
real facts about its user. Apply the two lists below before considering
any update.

**Return `[]` (no updates) when the source is any of:**

- Greetings: "hallo", "hi", "moin", "guten morgen", "ciao", "tschuess",
  "hello", "hey", "good morning".
- Status questions: "wie geht's", "alles ok?", "wie laeuft's",
  "how are you", "everything good?".
- Tool-acknowledgements / one-shot commands: "ok", "klar", "mach das",
  "ja", "nein", "danke", "thanks", "noted", "got it" -- as the WHOLE
  utterance, not as a prefix to a fact.
- Smalltalk without information content: weather chat, jokes, generic
  encouragement, filler talk ("hmm interessant", "echt jetzt?").
- Ephemeral micro-moments unless explicitly elevated: "ich trink
  gerade Kaffee", "bin auf dem Klo" -- skip.
- Repeats of facts that already appear unchanged on the target page.

**Write notes when the source contains any of:**

- People + properties: a person's name plus age, role, hobby, family
  link, contact, employer, location, traits.
- Dates / appointments / commitments: anything with a temporal anchor
  ("morgen", "naechste Woche", "am 14.05.", "every Tuesday").
- Places: cities, countries, venues the user mentions in a way that
  ties them to a person or project.
- Preferences and habits: favourite anything (food, film, music,
  tool), dietary restrictions, working style, schedule patterns.
- Decisions / rules / from-now-on statements: provider switches,
  workflow changes, "I'm going to stop using X".
- Project updates: status changes on active workstreams, milestones,
  blockers.
- Relationships: who-knows-whom, family, employer, collaborator.

When in doubt: write the note. Forgetting a fact is a strictly worse
failure than adding a slightly-too-eager one (the user can always edit
or archive a wiki page; they cannot un-forget).

## Output Contract (binding)

Return ONLY a single JSON array. No prose before or after. No code
fences. The array contains zero or more update objects shaped like:

    [
      {
        "target": "entities/<slug>.md",
        "operation": "create" | "update" | "rename" | "archive",
        "new_body": "<full markdown body with frontmatter>",
        "rename_from": null | "entities/<old-slug>.md",
        "reason": "<one short sentence>"
      }
    ]

Rules:
- If the source is smalltalk, ack-only, or content-free, return `[]`
  with no commentary.
- Never break a `[[wikilink]]`. If a link would resolve nowhere, also
  emit the missing target as a `create` update in the same array.
- Every `new_body` must conform to `schema.md` (frontmatter keys, body
  sections, page-type rules).
- Never touch `_archive/` or `attachments/`. Never write secrets.
- The whole response stays below the output-token budget; pick fewer,
  smaller updates rather than truncating the JSON.

## What counts as worth-saving (positive examples)

These are all valid sources to persist as wiki updates — do NOT
dismiss them as smalltalk:

- **User identity facts**: preferences, hobbies, habits, opinions,
  dietary restrictions, schedule patterns, working style.  These
  belong on the user's own entity page (default slug `entities/ruben.md`,
  create if missing).  Examples that MUST persist:
    * "Mein Lieblingsessen ist Pizza"      → entities/ruben.md, Facts/Preferences
    * "Ich hasse fruehe Meetings"          → entities/ruben.md, Facts/Schedule
    * "Ich arbeite immer mit Spotify"      → entities/ruben.md, Facts/Habits
- **Other-person identity facts**: same shape, just on their own
  entity page.
    * "Harald wurde 1976 geboren"          → entities/harald.md
    * "Mein Boss heisst Tom"               → entities/tom.md + relationship
- **Active projects / undertakings**: anything the user describes as
  current work-in-progress.
    * "Ich arbeite an einem Pixel-Art-Editor" → projects/pixel-art-editor.md
- **Dated commitments / decisions / rules**: things with a temporal
  anchor or a "from now on" flavour.
    * "Naechste Woche fahre ich nach Berlin"  → entities/ruben.md, Schedule
    * "Ab heute nutze ich nur noch Provider X" → concepts/<decision>.md

## What to skip

- Greetings, status questions, weather-talk, single-word answers.
- Ephemeral daily moments ("Ich habe gerade Kaffee getrunken") unless
  the user explicitly elevates them ("Notier dass ich heute…").
- Repeats of facts already present unchanged on the target page.
"""

# Top-N slugs the user prompt mentions for the LLM. Larger numbers
# bloat the user message; smaller numbers waste retrieval signal.
_TOP_SLUG_HINT_LIMIT = 10

# Compact slug-listing cap inside the vault summary header. Keeps the
# system prompt below the 8 000-token target.
_LATEST_SLUGS_PER_TYPE = 5

# Number of recent log entries to surface in the vault summary.
_RECENT_LOG_ENTRIES = 3

# Stop-word set for the keyword-overlap top-slugs ranker. German + English
# function words that appear in almost every source and would dominate the
# overlap score without carrying signal. Lowercase, ASCII-fold-ready.
_STOPWORDS: frozenset[str] = frozenset({
    # German
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
    "und", "oder", "aber", "doch", "nicht", "kein", "keine", "ist",
    "war", "sind", "wird", "wurde", "werden", "hat", "hatte", "haben",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mir", "mich",
    "dich", "dir", "uns", "euch", "ihm", "ihn", "ihnen",
    "mit", "ohne", "fuer", "für", "von", "vom", "zum", "zur", "zu",
    "auf", "aus", "bei", "nach", "vor", "ueber", "über", "unter",
    "an", "in", "im", "am", "als", "wie", "was", "wer", "wenn",
    "weil", "noch", "schon", "auch", "nur", "mehr", "sehr",
    # English
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "by",
    "from", "as", "at", "or", "and", "but", "if", "then", "than",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "doing",
    "this", "that", "these", "those", "it", "its", "they", "them",
    "i", "you", "we", "he", "she", "his", "her", "their", "our",
    "not", "no", "yes",
})

# Matches kebab-, snake_, dot-, underscore-, or whitespace-delimited
# tokens. Keeps ASCII letters/digits; drops punctuation.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _normalise_tokens(text: str) -> set[str]:
    """Lowercased alphanumeric tokens with the stop-words removed."""

    tokens = {tok.lower() for tok in _TOKEN_RE.findall(text)}
    return {tok for tok in tokens if len(tok) > 2 and tok not in _STOPWORDS}


def _slug_tokens(slug: str) -> set[str]:
    """Slug expanded into searchable tokens (the slug itself + its parts)."""

    parts = _normalise_tokens(slug)
    parts.add(slug.lower())
    return parts


def select_top_slugs(
    source_content: str,
    candidate_slugs: Iterable[str],
    *,
    limit: int = _TOP_SLUG_HINT_LIMIT,
) -> list[str]:
    """Rank vault slugs by keyword-overlap with the source content.

    Cheap O(n) ranking: tokenise both sides, count common tokens, sort
    descending. Ties broken alphabetically so the output is
    deterministic across runs (test-friendly). Returns at most ``limit``
    slugs; slugs with zero overlap are dropped.
    """

    src_tokens = _normalise_tokens(source_content)
    if not src_tokens:
        return []

    scored: list[tuple[int, str]] = []
    for slug in candidate_slugs:
        overlap = len(_slug_tokens(slug) & src_tokens)
        if overlap > 0:
            scored.append((overlap, slug))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [slug for _, slug in scored[:limit]]


def _format_slug_list(slugs: list[str], cap: int = _LATEST_SLUGS_PER_TYPE) -> str:
    """Render a comma-separated preview of up to ``cap`` slugs."""

    if not slugs:
        return "(none)"
    head = slugs[:cap]
    rendered = ", ".join(head)
    return f"{rendered}{', ...' if len(slugs) > cap else ''}"


def _read_recent_log_entries(
    log_path: Path, max_entries: int = _RECENT_LOG_ENTRIES,
) -> list[str]:
    """Return up to ``max_entries`` most-recent ``## [...]`` headings.

    Reads ``log.md`` synchronously — callers schedule it on a worker
    thread when invoked from an event loop. Failures (missing file,
    permission error) degrade to an empty list rather than raising.
    """

    try:
        text = log_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # Append-only log: walk from the end, collect the last N H2 headings.
    headings: list[str] = []
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("## [") and stripped.endswith(")") is False:
            headings.append(stripped[3:].strip())  # drop "## " prefix
            if len(headings) >= max_entries:
                break

    headings.reverse()
    return headings


def compute_vault_summary(
    vault: Any,
    *,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Snapshot the vault state for the system-prompt header.

    Returns a dict with three keys:

    * ``counts`` — ``{page_type: int}`` for entity/concept/project/session.
    * ``latest`` — ``{page_type: [slug, ...]}``, alphabetically sorted,
      capped at ``_LATEST_SLUGS_PER_TYPE`` slugs per type.
    * ``recent_log`` — list of up to three log-entry headings, oldest
      first (chronological).

    The function is forgiving: a ``VaultIndex`` without one of the page
    types simply contributes a zero count. A missing or unreadable
    ``log.md`` yields an empty ``recent_log``.
    """

    page_types = ("entity", "concept", "project", "session")
    counts: dict[str, int] = {}
    latest: dict[str, list[str]] = {}

    for ptype in page_types:
        try:
            pages = vault.pages_by_type(ptype) or []
        except Exception:                                         # noqa: BLE001
            pages = []
        slugs = sorted({getattr(p, "slug", "") for p in pages if getattr(p, "slug", "")})
        counts[ptype] = len(slugs)
        latest[ptype] = slugs[:_LATEST_SLUGS_PER_TYPE]

    recent_log: list[str] = []
    if log_path is not None:
        recent_log = _read_recent_log_entries(log_path)

    return {
        "counts": counts,
        "latest": latest,
        "recent_log": recent_log,
    }


def _render_vault_summary(summary: dict[str, Any]) -> str:
    """Plain-text block describing ``counts`` / ``latest`` / ``recent_log``."""

    counts: dict[str, int] = summary.get("counts", {})
    latest: dict[str, list[str]] = summary.get("latest", {})
    recent: list[str] = summary.get("recent_log", [])

    lines: list[str] = ["## Current Vault Snapshot", ""]
    for ptype, label in (
        ("entity", "Entities"),
        ("concept", "Concepts"),
        ("project", "Projects"),
        ("session", "Sessions"),
    ):
        count = counts.get(ptype, 0)
        slugs = _format_slug_list(latest.get(ptype, []))
        lines.append(f"- {label}: {count} (sample: {slugs})")

    lines.append("")
    lines.append("Recent log entries (oldest first):")
    if recent:
        for heading in recent:
            lines.append(f"  - {heading}")
    else:
        lines.append("  - (empty log)")
    return "\n".join(lines)


def build_system_prompt(
    schema_md: str,
    vault_summary: dict[str, Any] | None = None,
) -> str:
    """Compose the system prompt from the schema, the vault summary, and the contract.

    The schema is included verbatim — the LLM is the contract interpreter,
    not the Python code. ``vault_summary`` is the dict returned by
    ``compute_vault_summary``; passing ``None`` skips the snapshot
    section (useful for tests). The output-contract block is always
    appended last so the LLM sees the structural requirements after the
    schema.
    """

    parts: list[str] = [
        "You are the long-term knowledge-wiki curator for Personal Jarvis.",
        "Your job: turn one new source into a small, coherent set of",
        "wiki page updates. Follow the schema below without exception.",
        "",
        "# Wiki Schema (binding contract)",
        "",
        schema_md.rstrip(),
    ]
    if vault_summary is not None:
        parts.append("")
        parts.append(_render_vault_summary(vault_summary))
    parts.append("")
    parts.append(_OUTPUT_CONTRACT)
    return "\n".join(parts)


def build_user_prompt(
    source_label: str,
    source_content: str,
    top_slugs: list[str] | None = None,
) -> str:
    """Wrap the source content in a turn template the LLM can act on.

    ``top_slugs`` is the keyword-overlap shortlist produced by
    ``select_top_slugs``. Empty list (or ``None``) renders an explicit
    "no overlap detected" hint so the LLM is told to consider creating
    fresh pages.
    """

    hints = top_slugs or []
    if hints:
        hint_lines = "\n".join(f"  - {slug}" for slug in hints)
        hint_block = (
            "Most likely affected pages (top-10 by keyword overlap):\n"
            f"{hint_lines}"
        )
    else:
        hint_block = (
            "Most likely affected pages (top-10 by keyword overlap):\n"
            "  - (no overlap detected — consider creating new pages)"
        )

    return (
        f"Source label: {source_label}\n"
        "Source content (verbatim):\n"
        "----- BEGIN SOURCE -----\n"
        f"{source_content}\n"
        "----- END SOURCE -----\n"
        "\n"
        f"{hint_block}\n"
        "\n"
        "Return the JSON array now."
    )


# ---------------------------------------------------------------------------
# Wave-2 Stage-2 consolidator prompt (body-aware ADD/UPDATE/NOOP/INVALIDATE)
# ---------------------------------------------------------------------------

_CONSOLIDATOR_SYSTEM = """\
You are the consolidating editor of a personal knowledge wiki (an Obsidian
vault). You receive a batch of CANDIDATE FACTS extracted from the user's
conversations, together with the FULL BODIES of the existing pages most
related to each candidate. Decide, per candidate, how the vault changes.

Return ONLY a JSON array. One object per candidate:
  {"candidate_id": <int>, "decision": "add" | "update" | "noop" | "invalidate",
   "target": "<dir>/<slug>.md", "new_body": "<full page markdown>",
   "superseded_by": "<slug>", "reason": "<short why>"}

Decision semantics:
- "add": the fact is NEW knowledge with no fitting existing page. Provide
  "target" (entities/, concepts/ or projects/) and a complete "new_body"
  (frontmatter + sections per the page-type template below).
- "update": the fact belongs on an existing page shown to you. Provide
  "target" and the page's complete "new_body" with the fact merged in.
  MAKE THE SMALLEST CORRECT EDIT: every existing fact, section and link
  of the shown body MUST survive unless the candidate directly corrects it.
- "noop": the vault already knows this (the shown bodies cover it).
  Provide only "candidate_id", "decision", "reason".
- "invalidate": the fact CONTRADICTS a shown page so that page (or
  statement) is now outdated. Provide "target" (the superseded page) and
  "superseded_by" (the slug of the page that replaces it — usually one you
  "add" or "update" in this same batch). Do NOT provide "new_body"; the
  system marks the page superseded mechanically. Nothing is ever deleted.

Page-type templates (frontmatter keys are mandatory):
- entities/<slug>.md: type: entity, entity_kind (person|tool|repository|
  service|device), slug, aliases, created, updated. Sections: # Name,
  ## Summary, ## Facts, ## Relationships, ## Sources.
- concepts/<slug>.md: type: concept, slug, aliases, created, updated.
  Sections: ## Summary, ## Definition, ## Examples, ## Related, ## Sources.
- projects/<slug>.md: type: project, slug, status, started, last_activity.
  Sections: ## Goal, ## Current Status, ## Recent Activity, ## Open Threads,
  ## Related, ## Sources.

Linking rules:
- Cross-link related pages with [[wikilinks]] in their Relationships /
  Related sections — when a fact connects two pages, update BOTH if both
  are shown to you.
- Only link pages that exist or that you create in THIS batch; anything
  else write as plain text.
- The user's profile page (the user entity) is the preferred "update"
  target for identity/preference facts about the user; keep its section
  structure intact.
- Never write credentials or secrets. No prose outside the JSON array.
"""


def build_consolidator_prompt(
    candidates: Iterable[Any],
    neighbours: dict[str, str],
    *,
    user_entity_slug: str = "",
) -> tuple[str, str]:
    """Build (system, user) prompts for the Stage-2 judge.

    ``candidates`` are journal rows (need ``.id``, ``.fact``, ``.kind``,
    ``.subjects``); ``neighbours`` maps a vault-relative path to the FULL
    page body (this is the body-awareness that the legacy curator lacked).
    Pure function — no I/O, no LLM call.
    """
    parts: list[str] = []
    # Anchor the model in real time: without this, frontmatter dates and
    # "in the spring of <year>" prose get hallucinated into the wrong year
    # (live finding 2026-06-10: pages created with created: 2024-*).
    import datetime as _dt

    parts.append(f"Today is {_dt.date.today().isoformat()}.\n")
    if user_entity_slug:
        parts.append(
            f"The user's profile page is entities/{user_entity_slug}.md.\n"
        )

    parts.append("----- EXISTING PAGES (full bodies) -----")
    if neighbours:
        for rel_path, body in neighbours.items():
            parts.append(f"=== {rel_path} ===\n{body.rstrip()}")
    else:
        parts.append("(no related pages found — the vault is empty here)")
    parts.append("----- END EXISTING PAGES -----\n")

    parts.append("----- CANDIDATE FACTS -----")
    for row in candidates:
        subjects = ", ".join(getattr(row, "subjects", ()) or ()) or "-"
        parts.append(
            f"candidate_id={row.id} kind={row.kind} subjects=[{subjects}]\n"
            f"  {row.fact}"
        )
    parts.append("----- END CANDIDATE FACTS -----\n")
    parts.append("Return the JSON array now (one object per candidate).")

    return _CONSOLIDATOR_SYSTEM, "\n".join(parts)


__all__ = [
    "build_consolidator_prompt",
    "build_system_prompt",
    "build_user_prompt",
    "compute_vault_summary",
    "select_top_slugs",
]
