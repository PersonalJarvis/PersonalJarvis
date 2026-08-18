"""Evidence gate — deterministic honesty guard for external-data domains.

Design: docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md
(AD-CLI4..AD-CLI8). Pure regex + in-memory registry lookups — NO LLM call,
NO disk/network IO (AP-9/AP-11). Called once per turn from
``BrainManager.generate()``; every failure path degrades to PASS.

Verdicts:
  pass            — turn proceeds unchanged (default for ~99% of turns).
  require_tool    — a connected CLI covers the matched domain: the manager
                    injects ``directive`` into this turn's system prompt.
  honest_refusal  — nothing covers the domain: the manager speaks
                    ``refusal_text`` deterministically (no LLM involved).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.core.capabilities import _normalize
from jarvis.core.turn_language import (
    DEFAULT_LOCALE,
    detect_text_language,
    normalize_language_tag,
)

log = logging.getLogger(__name__)

# A domain keyword alone must not trigger (hard negative: "Ich habe dir das
# per Mail geschickt" mentions mail in passing). The utterance must also look
# like a question/lookup or a read-imperative on the domain.
_LOOKUP_SHAPE_RE = re.compile(
    r"\b(was|wann|welche|welcher|welches|wie viele|wieviele|gibt es|gibts|"
    r"hab ich|habe ich|steht|stehen|ansteht|anstehen|zeig|zeige|check|checke|"
    r"pruef|pruefe|liste|list|lies|lese|fasse|what|when|which|how many|"
    r"do i have|any|anything|is there|are there|show|summarize|read)\b"
)

# Definitional/explanatory questions are general knowledge, not a data lookup
# (e.g. the German "Was ist ein Pull Request?") — never force a tool call for them.  # i18n-allow: quoted German input example
_DEFINITION_RE = re.compile(
    r"\b(was ist ein|was ist eine|was sind|was bedeutet|wofuer steht|"  # i18n-allow: German input-matching data
    r"what is a|what is an|what are|what does|explain|erklaer)\b"
)

# A possessive/ownership marker turns a "was sind …" phrasing into a personal
# data lookup ("Was sind meine Abrechnungen?"), not a definition — it must
# defeat the definitional short-circuit above (live 2026-06-17 billing query).
_OWNERSHIP_RE = re.compile(
    r"\b(mein|meine|meinem|meinen|meines|my|our|unser|unsere|unserem|unseren)\b"
)


# Everything that is not a letter or a digit separates two identifier tokens:
# "mcp__google_calendar__list-events" carries the tokens that matter.
_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")

# Below this length a separator-free substring match is noise ("pr" would hit
# "switch_provider"), so short terms are matched as whole tokens only. Four is
# the lowest useful bar: it is what makes "mail" find the tool named "gmail".
_MIN_SQUASHED_TERM_LEN = 4

# Words that name the SAME system. Purely lexical — the user says "Termin",
# the tool is called "google_calendar"; the user says "E-Mail", the tool is
# called "gmail". Without this bridge a name-only match misses the most common
# real integrations, which is precisely the false refusal being fixed. Each
# group is one system: WhatsApp and Telegram stay apart, because handing a
# Telegram tool a WhatsApp request would be a wrong call, not a lenient one.
_SYSTEM_SYNONYMS: tuple[frozenset[str], ...] = (
    frozenset({
        "mail", "mails", "e-mail", "e-mails", "email", "emails", "gmail",
        "outlook", "postfach", "posteingang", "inbox", "mailbox",
    }),
    frozenset({
        "kalender", "calendar", "termin", "termine", "appointment",
        "appointments", "meeting", "meetings", "gcal",
    }),
    frozenset({
        "aufgabe", "aufgaben", "todo", "todos", "to-do", "task", "tasks",
        "reminder", "reminders", "todoist",
    }),
    frozenset({
        "repo", "repos", "repository", "repositories", "pull request",
        "pull requests", "issue", "issues", "github", "gitlab",
    }),
    # One domain, two systems: "musik" must find a lone Spotify OR a lone
    # YouTube Music (the tool name carries "music", the brand terms do not).
    frozenset({
        "spotify", "musik", "music", "youtube music", "youtube_music", "yt music", "ytmusic",
    }),
    frozenset({"twitter", "x", "tweet", "tweets"}),
    frozenset({
        "pizza", "lieferando", "doordash", "uber eats", "ubereats", "wolt",
        "lieferservice",
    }),
    frozenset({
        "flug", "fluege", "flight", "flights", "hotel", "hotels", "airbnb",
        "booking", "trip", "trips", "reise", "reisen", "travel", "ticket",
        "tickets",
    }),
    frozenset({
        "tisch", "restaurant", "reservierung", "reservation", "opentable",
        "resy",
    }),
    frozenset({"deployment", "deployments", "deploy", "vercel", "netlify"}),
)

_SYNONYM_INDEX: dict[str, frozenset[str]] = {
    _normalize(word): group for group in _SYSTEM_SYNONYMS for word in group
}


def live_surface_covers(terms: Iterable[str], tool_names: Iterable[str]) -> bool:
    """True when a tool ATTACHED TO THIS TURN plausibly serves ``terms``.

    Ground truth for every honest refusal (GT-16/GT-17). The refusals used to
    consult the capability registry alone, and that registry lags reality: a
    plugin, CLI or MCP server connected mid-session is callable immediately
    while the registry still knows nothing about it, so the user was told "I
    can't do that" with the tool sitting right there in the tool surface.

    The rule is deliberately shallow. A tool's REGISTERED NAME is its identity
    — "gmail", "google_calendar", "spotify", "mcp__todoist__add_task" — and a
    connected integration is named after the system it serves. A term matches
    when its tokens appear as a run inside the name, or, from
    ``_MIN_SQUASHED_TERM_LEN`` characters up, when the separator-free forms
    overlap, so "whats-app" still finds a tool called ``whatsapp``. Each term
    is first widened by ``_SYSTEM_SYNONYMS``, the lexical bridge between what
    the user says and what the tool is called.

    Descriptions are NOT searched: everyday words from the domain lists
    ("task", "post", "repository", "issue") appear in the prose of unrelated
    tools and would silently disable every refusal — trading one dishonest
    answer for another. This is not a second guessing layer: it decides only
    whether to SPEAK a refusal, never which tool to call. A false positive
    costs one ordinary model turn; a false negative is the bug being fixed.
    """
    haystacks: list[tuple[str, str]] = []
    for raw_name in tool_names:
        name = _normalize(str(raw_name or ""))
        spaced = _SEPARATOR_RE.sub(" ", name).strip()
        if spaced:
            haystacks.append((spaced, _SEPARATOR_RE.sub("", name)))
    if not haystacks:
        return False

    wanted: set[str] = set()
    for raw_term in terms:
        term = _normalize(str(raw_term or "")).strip()
        if not term:
            continue
        wanted.add(term)
        wanted.update(_SYNONYM_INDEX.get(term, ()))

    for term in wanted:
        spaced_term = _SEPARATOR_RE.sub(" ", term).strip()
        if not spaced_term:
            continue
        squashed_term = _SEPARATOR_RE.sub("", term)
        token_re = re.compile(r"\b" + re.escape(spaced_term) + r"\b")
        for name_spaced, name_squashed in haystacks:
            if token_re.search(name_spaced):
                return True
            if len(squashed_term) >= _MIN_SQUASHED_TERM_LEN and squashed_term in name_squashed:
                return True
    return False


@dataclass(frozen=True)
class EvidenceVerdict:
    kind: Literal["pass", "require_tool", "honest_refusal"]
    domain: str = ""
    tool_name: str = ""
    directive: str = ""
    refusal_text: str = ""


_PASS = EvidenceVerdict(kind="pass")

# Spoken German voice replies (TTS-safe, deterministic).
_REFUSAL_DE: dict[str, str] = {
    "calendar": "Ich habe aktuell keinen Kalenderzugriff.",  # i18n-allow
    "email": "Ich habe aktuell keinen Zugriff auf dein Postfach.",  # i18n-allow
    "tasks": "Ich habe aktuell keinen Zugriff auf deine Aufgaben.",  # i18n-allow
    "repos": "Ich habe aktuell keinen Zugriff auf deine Repositories.",  # i18n-allow
    "deployments": "Ich habe aktuell keinen Zugriff auf deine Deployments.",  # i18n-allow
    "cloud": "Ich habe aktuell keinen Zugriff auf deine Cloud-Abrechnung.",  # i18n-allow
    "activity": "Ich kann gerade nicht auf deinen Aktivitätsverlauf zugreifen.",  # i18n-allow
}
_REFUSAL_DE_FALLBACK = "Dafuer habe ich aktuell keinen Datenzugriff."  # i18n-allow

_REFUSAL_EN: dict[str, str] = {
    "calendar": "I have no calendar access right now.",
    "email": "I have no access to your inbox right now.",
    "tasks": "I have no access to your tasks right now.",
    "repos": "I have no access to your repositories right now.",
    "deployments": "I have no access to your deployments right now.",
    "cloud": "I have no access to your cloud billing right now.",
    "activity": "I can't access your activity history right now.",
}
_REFUSAL_EN_FALLBACK = "I have no data access for that right now."

# Spoken Spanish voice replies (TTS-safe, deterministic). All locales are
# equal (CLAUDE.md §1): a Spanish-speaking user gets the refusal in Spanish,
# not the English table because no Spanish one existed.
_REFUSAL_ES: dict[str, str] = {
    "calendar": "Ahora mismo no tengo acceso a tu calendario.",  # i18n-allow
    "email": "Ahora mismo no tengo acceso a tu correo.",  # i18n-allow
    "tasks": "Ahora mismo no tengo acceso a tus tareas.",  # i18n-allow
    "repos": "Ahora mismo no tengo acceso a tus repositorios.",  # i18n-allow
    "deployments": "Ahora mismo no tengo acceso a tus despliegues.",  # i18n-allow
    "cloud": "Ahora mismo no tengo acceso a tu facturación en la nube.",  # i18n-allow
    "activity": "Ahora mismo no puedo acceder a tu historial de actividad.",  # i18n-allow
}
_REFUSAL_ES_FALLBACK = "Ahora mismo no tengo acceso a esos datos."  # i18n-allow

#: One table per supported locale, so adding a language is a table, not a
#: branch. Keys must stay in step with ``jarvis.core.turn_language``'s pins.
_REFUSALS: dict[str, tuple[dict[str, str], str]] = {
    "de": (_REFUSAL_DE, _REFUSAL_DE_FALLBACK),
    "en": (_REFUSAL_EN, _REFUSAL_EN_FALLBACK),
    "es": (_REFUSAL_ES, _REFUSAL_ES_FALLBACK),
}


def _refusal_language(resolved: object, text: str) -> str:
    """Which language the deterministic refusal is spoken in.

    The turn's ALREADY-resolved output language wins outright — this module
    must never re-derive it (CLAUDE.md §1: one resolver,
    ``resolve_output_language``, decides for all layers). It used to sniff the
    utterance with a private de/en-only heuristic, so an explicit
    ``brain.reply_language`` pin was ignored: a Spanish-pinned user asking in
    English heard an English refusal, and there was no Spanish table to reach
    at all.

    The text fallback exists only for callers not yet passing ``language``. It
    is the canonical detector rather than a local one, and an undecidable text
    lands on the shared ``DEFAULT_LOCALE`` instead of a hardcoded per-layer
    default.
    """
    code = normalize_language_tag(resolved)
    if code in _REFUSALS:
        return code
    detected = detect_text_language(text)
    return detected if detected in _REFUSALS else DEFAULT_LOCALE


def check_evidence_domain(
    text: str,
    *,
    enabled: bool,
    domains: Mapping[str, Sequence[str]],
    capability_registry: Any,
    domain_tool_map: Mapping[str, str],
    refusal_hint_fn: Callable[[str, str], str] | None = None,
    live_tool_names: Sequence[str] = (),
    language: object = "",
) -> EvidenceVerdict:
    """Classify one utterance against the evidence-required domains.

    ``live_tool_names`` are the names of the tools attached to THIS turn, read
    at decision time. They are the last word before a refusal is spoken: a
    matching tool on the live surface beats an empty registry (see
    :func:`live_surface_covers`). Defaulting to ``()`` keeps every existing
    caller on the registry-only behaviour.

    ``language`` is THIS turn's resolved output language (de/en/es), produced
    by ``jarvis.core.turn_language.resolve_output_language`` and passed down by
    the caller. The honest refusal is user-facing speech, so it must be spoken
    in the same language as every other layer of the turn. Callers that omit it
    fall back to detecting the utterance (see :func:`_refusal_language`), which
    silently ignores an explicit ``brain.reply_language`` pin — pass it.
    """
    if not enabled:
        return _PASS
    t = (text or "").strip()
    if not t:
        return _PASS
    normalised = _normalize(t)
    if _DEFINITION_RE.search(normalised) and not _OWNERSHIP_RE.search(normalised):
        return _PASS
    if not _LOOKUP_SHAPE_RE.search(normalised):
        return _PASS

    matched_domain = ""
    for domain, keywords in domains.items():
        if any(re.search(r"\b" + re.escape(_normalize(kw)) + r"\b", normalised) for kw in keywords):
            matched_domain = domain
            break
    if not matched_domain:
        return _PASS

    # CLI-first preference (req 4, supersedes AD-CLI6): a connected CLI for the
    # domain ALWAYS wins over a plugin/skill — a CLI runs a local subprocess and
    # is cheaper than a plugin's MCP/HTTP/API round-trip. Plugins are fallback
    # only, so we mandate the CLI before considering any non-CLI capability.
    tool_name = domain_tool_map.get(matched_domain, "")
    if tool_name:
        directive = (
            f"MANDATORY THIS TURN: the user is asking about {matched_domain} "
            f"data. You MUST call the `{tool_name}` tool (read-only command, "
            f"prefer a --json/--format json output flag) BEFORE answering, "
            f"and answer ONLY from its result. If the call fails, say that it "
            f"failed and why — NEVER invent {matched_domain} data."
        )
        return EvidenceVerdict(
            kind="require_tool",
            domain=matched_domain,
            tool_name=tool_name,
            directive=directive,
        )

    # No CLI covers the domain: a non-CLI capability (paired skill / MCP plugin)
    # owns the turn — let the existing machinery handle it (the fallback).
    domain_keywords = [_normalize(k) for k in domains[matched_domain]]
    try:
        caps = capability_registry.all() if capability_registry is not None else ()
    except Exception:  # noqa: BLE001 — registry fault degrades to PASS
        return _PASS
    for cap in caps:
        if getattr(cap, "source", "") == "cli":
            continue
        objs = {_normalize(o) for o in getattr(cap, "objects", ())}
        if matched_domain in objs or objs.intersection(domain_keywords):
            return _PASS

    # Last stop before the refusal (GT-16): the registry said "nothing covers
    # this domain", but the registry is a cache and the tool surface is the
    # truth. A plugin/CLI/MCP tool connected seconds ago is callable now and
    # unknown to the registry — refusing then is the exact bug the maintainer
    # reported. Stand down and let the model call the tool; if it turns out
    # dead, the tool's own result still fails honestly.
    if live_surface_covers([matched_domain, *domains[matched_domain]], live_tool_names):
        log.info(
            "Evidence gate stood down: domain=%s is covered by a tool on the "
            "live surface, so the honest refusal would have been wrong.",
            matched_domain,
        )
        return _PASS

    lang = _refusal_language(language, t)
    table, table_fallback = _REFUSALS[lang]
    base = table.get(matched_domain, table_fallback)
    hint = ""
    if refusal_hint_fn is not None:
        try:
            hint = refusal_hint_fn(matched_domain, lang) or ""
        except Exception:  # noqa: BLE001
            hint = ""
    return EvidenceVerdict(
        kind="honest_refusal",
        domain=matched_domain,
        refusal_text=base + hint,
    )


__all__ = ["EvidenceVerdict", "check_evidence_domain", "live_surface_covers"]
