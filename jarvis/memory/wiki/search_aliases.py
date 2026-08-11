"""Search aliases — the language bridge between the user and their vault.

The problem, measured
---------------------
The fact extractor writes vault pages in English; the user asks in their own
language. A keyword index cannot cross that gap: "Flugzeuge" never matches
"aircraft", and a page called ``nvidia-geforce-rtx-5070-ti`` is unreachable
from the word "Grafikkarte". Against the maintainer's real 26-page vault on
2026-07-25, twelve ordinary questions about his own life recalled the right
page 4 times; a controlled probe put German at 0/7 and the SAME questions in
English at 7/7. The knowledge was there — only the key was in another
language.

The bridge
----------
Each page carries a ``search_aliases`` frontmatter list holding the words a
user would actually SAY when looking for it, in every language they use. The
indexer already flattens every frontmatter value (lists included) into the
indexed ``frontmatter`` column, weighted above body text, so nothing about
the schema or the index has to change — writing the field is enough.

Design rule: **pay at write time, not at ask time.** Generating aliases costs
one small LLM call, made rarely and in the background; answering then costs
nothing at all. The alternative — translating the query on every question —
would put a model call on the latency path of every single turn, which is
exactly what the voice budget cannot afford (AP-9/AP-26).

Universality (§3): the generator degrades to "no aliases" whenever no
provider is reachable — never an exception, never a blocked write. A vault
without aliases behaves exactly as it does today. Provider selection goes
through the shared key-aware wiki chain, so a dead or throttled provider
crosses to another family instead of bricking the feature (AP-22).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jarvis.core.turn_language import DEFAULT_LOCALE

log = logging.getLogger(__name__)

__all__ = [
    "ALIAS_FIELD",
    "MAX_ALIASES",
    "backfill_vault",
    "build_alias_prompt",
    "ensure_page_aliases",
    "generate_aliases",
    "merge_aliases",
    "parse_alias_response",
    "target_languages",
]

#: Frontmatter key holding the bridge terms.
ALIAS_FIELD = "search_aliases"

#: Upper bound per page. Aliases are weighted above body text, so an
#: unbounded list would let one page shadow the whole vault.
MAX_ALIASES = 12

#: Longest single alias worth indexing. Anything longer is a sentence, not a
#: search term, and only dilutes the page's frontmatter column.
_MAX_ALIAS_CHARS = 40

#: Aliases are matching data, never prose — reject anything with markup,
#: separators, or list syntax that indicates the model returned a sentence.
_ALIAS_RE = re.compile(r"^[\w\s\-']{2,}$", re.UNICODE)

#: Pronouns and bare function words are worthless as aliases and actively
#: harmful: they appear in almost every personal question, so a page carrying
#: them becomes a magnet that surfaces for anything. Observed on the first
#: real run — the user's own entity page was offered "ich", "me", "myself".
#: Kept deliberately tiny: this rejects words that identify NO page, not words
#: that merely feel generic ("auto" genuinely identifies a car page).
_USELESS_ALIASES: frozenset[str] = frozenset(
    {
        # de
        "ich", "mir", "mich", "wir", "uns", "du", "er", "sie", "es", "man",
        "mein", "meine", "selbst",
        # en
        "i", "me", "my", "mine", "myself", "we", "us", "our", "you", "it",
        "self", "own", "thing", "stuff",
        # es ("me"/"mi" already listed above)
        "yo", "nosotros", "tu", "el", "ella", "eso",
    }
)


#: Language signals to harvest, most authoritative first. Each is a dotted
#: config path; "auto"/empty values are ignored.
_LANGUAGE_SIGNALS: tuple[str, ...] = (
    "brain.reply_language",  # what Jarvis is pinned to SPEAK
    "ui.language",  # the interface the user chose
    "stt.language",  # what the recogniser is pinned to HEAR
    "stt.wake_language",  # the wake-word language — often the only honest hint
)


def _config_path(cfg: Any, dotted: str) -> str:
    node: Any = cfg
    for part in dotted.split("."):
        node = getattr(node, part, None)
        if node is None:
            return ""
    return str(node).strip().lower()[:2]


def target_languages(cfg: Any) -> tuple[str, ...]:
    """Languages a page's aliases should cover, most important first.

    There is no single field that states which language the user actually
    speaks, and guessing from one of them is how this feature silently does
    nothing: on the maintainer's own box ``reply_language`` is "auto",
    ``ui.language`` and ``stt.language`` are both "en", and only
    ``wake_language`` reveals a German speaker. Resolving from
    ``reply_language`` alone produced English aliases for an already-English
    vault — a bridge to nowhere.

    So the explicit ``wiki_integration.search_alias_languages`` list wins when
    set, and otherwise every signal is harvested rather than ranked-and-picked.
    English is always included last because the vault itself is written in it:
    an English alias costs nothing and keeps English questions working.
    """
    langs: list[str] = []
    explicit = getattr(
        getattr(cfg, "wiki_integration", None), "search_alias_languages", None
    )
    for item in explicit or []:
        code = str(item).strip().lower()[:2]
        if code and code not in langs:
            langs.append(code)
    if not langs:
        for path in _LANGUAGE_SIGNALS:
            value = _config_path(cfg, path)
            if value and value != "au" and value not in langs:  # "auto" -> "au"
                langs.append(value)
    if not langs:
        langs.append((DEFAULT_LOCALE or "en").lower()[:2])
    if "en" in langs:
        langs.remove("en")
    langs.append("en")
    return tuple(langs)


def build_alias_prompt(*, title: str, body: str, languages: tuple[str, ...]) -> str:
    """Instruction for the alias model call.

    Deliberately asks for SEARCH TERMS, not translations: the user does not
    look for "aircraft" translated, they look for "Flugzeuge", "Jet", or
    "fliegen". Everyday words and the obvious category word beat a faithful
    rendering of the title.
    """
    langs = ", ".join(languages)
    excerpt = body.strip()[:1200]
    return (
        "You generate SEARCH ALIASES for a page in a personal knowledge vault.\n"
        "\n"
        "The vault is written in English, but its owner searches in their own "
        f"language. Produce the words they would actually SAY or TYPE when "
        f"looking for this page, in these languages: {langs}.\n"
        "\n"
        "Rules:\n"
        "- Everyday words, not translations of the title. A page about an "
        "'NVIDIA GeForce RTX 5070 Ti' should yield the category word for a "
        "graphics card in each language, not a transliterated product name.\n"
        "- Include the obvious category word, common synonyms, and the "
        "colloquial short form people really use.\n"
        "- Include the VERB people use when asking, not only nouns. Someone "
        "asks 'where do I live', not 'residence' — so a page about where its "
        "owner lives needs the verb form ('wohnen'), and one about a planned "
        "move needs 'umziehen', not just 'Umzug'. Give the plain dictionary "
        "form of the verb; matching handles the endings.\n"
        f"- At most {MAX_ALIASES} terms, each at most three words.\n"
        "- No sentences, no explanations, no markdown, no numbering.\n"
        "- Do NOT repeat words already in the title — those are already "
        "searchable.\n"
        "- If nothing useful applies, return nothing at all.\n"
        "\n"
        "Answer with ONE term per line and nothing else.\n"
        "\n"
        f"Title: {title}\n"
        f"Content:\n{excerpt}\n"
    )


def parse_alias_response(text: str, *, title: str = "") -> list[str]:
    """Turn a model answer into a clean, bounded alias list.

    Tolerant of the usual model habits (bullets, numbering, quotes, a stray
    "Here are the terms:" line) because a malformed answer must degrade to
    fewer aliases, never to an exception on the write path.
    """
    if not text:
        return []
    title_words = {w.lower() for w in re.findall(r"\w+", title)}
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        # Strip the list marker BEFORE the quotes: a quoted item behind a
        # marker ('2) "Graka"') still has its quotes attached at this point,
        # so stripping quotes first would leave them embedded and the term
        # would be rejected as markup.
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line.strip()).strip()
        line = line.strip("\"'").strip()
        if not line or len(line) > _MAX_ALIAS_CHARS:
            continue
        if not _ALIAS_RE.match(line) or len(line.split()) > 3:
            continue
        key = line.lower()
        # A term the title already contains adds nothing — it is indexed
        # anyway, at a higher weight. A pronoun identifies no page at all.
        if key in seen or key in title_words or key in _USELESS_ALIASES:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= MAX_ALIASES:
            break
    return out


def merge_aliases(existing: Any, generated: list[str]) -> list[str]:
    """Union of hand-written and generated aliases, existing ones first.

    Never drops what a human put there: the field is user-editable in
    Obsidian, and a regeneration that silently deleted a curated term would
    make the feature untrustworthy.
    """
    out: list[str] = []
    seen: set[str] = set()
    for source in (existing if isinstance(existing, list) else [], generated):
        for item in source:
            term = str(item).strip()
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            out.append(term)
    return out[:MAX_ALIASES]


async def generate_aliases(
    *,
    title: str,
    body: str,
    cfg: Any,
    registry: Any,
    languages: tuple[str, ...] | None = None,
    timeout_s: float = 30.0,
) -> list[str]:
    """Search terms for one page, or ``[]`` when no provider is reachable.

    Never raises. Every failure mode — no configured provider, no credential,
    the whole chain exhausted, a malformed answer — returns an empty list with
    a logged reason, because a page without aliases is merely as findable as
    it is today, while a raising generator would block the write itself.
    """
    if not title.strip() and not body.strip():
        return []
    langs = languages or target_languages(cfg)

    from jarvis.core.protocols import BrainMessage, BrainRequest  # noqa: PLC0415
    from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415
        build_wiki_provider_chain,
        complete_with_fallback,
        credential_ready_wiki_providers,
    )

    # Same key-aware chain the curator uses: lead with the configured wiki
    # provider, then every other registered provider that actually holds a
    # credential (AP-22 — never a single-provider brick).
    try:
        available = set(registry.available())
        chain = build_wiki_provider_chain(
            primary=str(getattr(getattr(cfg, "brain", None), "primary", "") or ""),
            model_override="",
            available=available,
            credential_ready=credential_ready_wiki_providers(
                available=available, config=cfg
            ),
        )
    except Exception:  # noqa: BLE001 — a broken chain build must not block a write
        log.warning("search_aliases: could not build a provider chain", exc_info=True)
        return []
    if not chain:
        log.info(
            "search_aliases: no credential-ready provider — page indexed "
            "without a language bridge"
        )
        return []

    prompt = build_alias_prompt(title=title, body=body, languages=langs)
    request = BrainRequest(
        messages=(BrainMessage(role="user", content=prompt),),
        max_tokens=300,
        temperature=0.2,
        reasoning_effort="none",
    )

    # The chain awaits ``aggregate(stream)``, so this must be the shared ASYNC
    # stream consumer — a synchronous one raises TypeError on every provider
    # in the chain and looks like a total provider outage.
    from jarvis.brain.streaming import aggregate  # noqa: PLC0415

    try:
        result = await complete_with_fallback(
            registry=registry,
            chain=chain,
            request=request,
            timeout_s=timeout_s,
            label="wiki-search-aliases",
            aggregate=aggregate,
        )
    except Exception:  # noqa: BLE001 — degrade to no aliases, never fail the write
        log.warning("search_aliases: alias call raised", exc_info=True)
        return []

    if result is None:
        log.info("search_aliases: provider chain exhausted — no aliases for %r", title)
        return []
    agg, provider = result
    aliases = parse_alias_response(getattr(agg, "text", "") or "", title=title)
    log.info(
        "search_aliases: %d term(s) for %r via %s (%s)",
        len(aliases),
        title,
        provider,
        ",".join(langs),
    )
    return aliases


# ---------------------------------------------------------------------------
# Write-time bridge
# ---------------------------------------------------------------------------


async def ensure_page_aliases(
    page_text: str,
    *,
    cfg: Any,
    registry: Any,
    timeout_s: float = 15.0,
) -> str:
    """Return *page_text* with a populated ``search_aliases`` frontmatter field.

    The write-time half of the language bridge: the consolidator routes every
    proposed add/update body through here, so a page is born findable in the
    user's own language instead of waiting for someone to run the backfill
    route by hand (which the 2026-08-11 recall audit found nobody does).

    Pages that already carry aliases pass through untouched — regeneration is
    the backfill's ``overwrite`` decision, never an implicit side effect of an
    unrelated fact landing on the page. Every failure mode returns the
    ORIGINAL text: a page without aliases is merely as findable as it is
    today, while raising here would block the write itself.
    """
    try:
        from jarvis.memory.frontmatter import (  # noqa: PLC0415
            parse_frontmatter,
            write_frontmatter,
        )

        meta, body = parse_frontmatter(page_text)
        if not meta:
            # No parseable frontmatter — the schema validator downstream owns
            # that verdict; injecting a field here would forge a frontmatter
            # block the judge never proposed.
            return page_text
        if meta.get(ALIAS_FIELD):
            return page_text

        title_match = re.search(r"^\s{0,3}#\s+(.+)", body, re.MULTILINE)
        title = (
            title_match.group(1).strip()
            if title_match
            else str(meta.get("slug", "") or "")
        )
        generated = await generate_aliases(
            title=title, body=body, cfg=cfg, registry=registry, timeout_s=timeout_s
        )
        if not generated:
            return page_text
        meta[ALIAS_FIELD] = merge_aliases(meta.get(ALIAS_FIELD), generated)
        return write_frontmatter(meta, body)
    except Exception:  # noqa: BLE001 — the bridge must never block a write
        log.warning(
            "search_aliases: write-time alias injection failed", exc_info=True
        )
        return page_text


# ---------------------------------------------------------------------------
# Backfill over an existing vault
# ---------------------------------------------------------------------------


async def backfill_vault(
    *,
    vault_root: Any,
    cfg: Any,
    registry: Any,
    dry_run: bool = True,
    limit: int | None = None,
    overwrite: bool = False,
    skip_dirs: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Add ``search_aliases`` to pages in an existing vault.

    Existing vaults were written before the bridge existed, so their pages are
    reachable only through their English words. This walks them once.

    ``dry_run`` (the default) reports what WOULD change and writes nothing —
    the field lands in the user's own Obsidian files, so the destructive
    direction must be the one they opt into. ``overwrite`` regenerates pages
    that already carry aliases; by default those are left alone, which makes
    the run resumable and cheap to repeat after a partial failure.

    Returns a summary dict; per-page failures are counted, never raised, so
    one unreadable page cannot abort the whole run.
    """
    from pathlib import Path  # noqa: PLC0415

    from jarvis.memory.frontmatter import parse_frontmatter, write_frontmatter  # noqa: PLC0415
    from jarvis.memory.wiki.fts_index import SKIP_INDEX_DIRS  # noqa: PLC0415

    root = Path(vault_root)
    skip = skip_dirs if skip_dirs is not None else SKIP_INDEX_DIRS
    summary: dict[str, Any] = {
        "scanned": 0,
        "updated": 0,
        "skipped_existing": 0,
        "no_aliases": 0,
        "failed": 0,
        "dry_run": bool(dry_run),
        "languages": list(target_languages(cfg)),
        "pages": [],
    }

    # noqa ASYNC240: this is an offline maintenance sweep, not a request path.
    # It is invoked from a background job / CLI where a blocking directory walk
    # is correct and an anyio dependency would buy nothing.
    for path in sorted(root.rglob("*.md")):  # noqa: ASYNC240
        if any(part in skip for part in path.relative_to(root).parts[:-1]):
            continue
        if limit is not None and summary["updated"] >= limit:
            break
        summary["scanned"] += 1
        try:
            raw = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            if meta.get(ALIAS_FIELD) and not overwrite:
                summary["skipped_existing"] += 1
                continue

            title_match = re.search(r"^\s{0,3}#\s+(.+)", body, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.stem
            generated = await generate_aliases(
                title=title, body=body, cfg=cfg, registry=registry
            )
            if not generated:
                summary["no_aliases"] += 1
                continue

            merged = merge_aliases(meta.get(ALIAS_FIELD), generated)
            summary["pages"].append(
                {"path": path.relative_to(root).as_posix(), "aliases": merged}
            )
            if not dry_run:
                meta[ALIAS_FIELD] = merged
                path.write_text(write_frontmatter(meta, body), encoding="utf-8")
            summary["updated"] += 1
        except Exception:  # noqa: BLE001 — one bad page must not abort the run
            summary["failed"] += 1
            log.warning("search_aliases: backfill failed for %s", path, exc_info=True)

    log.info(
        "search_aliases backfill: scanned=%d updated=%d skipped=%d none=%d failed=%d "
        "dry_run=%s",
        summary["scanned"],
        summary["updated"],
        summary["skipped_existing"],
        summary["no_aliases"],
        summary["failed"],
        dry_run,
    )
    return summary
