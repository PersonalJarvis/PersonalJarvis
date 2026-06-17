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

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from jarvis.core.capabilities import _normalize

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
# ("Was ist ein Pull Request?") — never force a tool call for them.
_DEFINITION_RE = re.compile(
    r"\b(was ist ein|was ist eine|was sind|was bedeutet|wofuer steht|"
    r"what is a|what is an|what are|what does|explain|erklaer)\b"
)

# A possessive/ownership marker turns a "was sind …" phrasing into a personal
# data lookup ("Was sind meine Abrechnungen?"), not a definition — it must
# defeat the definitional short-circuit above (live 2026-06-17 billing query).
_OWNERSHIP_RE = re.compile(
    r"\b(mein|meine|meinem|meinen|meines|my|our|unser|unsere|unserem|unseren)\b"
)


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
}
_REFUSAL_DE_FALLBACK = "Dafuer habe ich aktuell keinen Datenzugriff."  # i18n-allow

_REFUSAL_EN: dict[str, str] = {
    "calendar": "I have no calendar access right now.",
    "email": "I have no access to your inbox right now.",
    "tasks": "I have no access to your tasks right now.",
    "repos": "I have no access to your repositories right now.",
    "deployments": "I have no access to your deployments right now.",
    "cloud": "I have no access to your cloud billing right now.",
}
_REFUSAL_EN_FALLBACK = "I have no data access for that right now."


def _detect_lang(text: str) -> str:
    """Cheap DE/EN heuristic for the refusal language (mirrors the existing
    heuristic in ``BrainManager._check_unsupported_intent``)."""
    if re.search(r"[äöüÄÖÜß]", text):
        return "de"
    if re.search(
        r"\b(was|wie|welche|welcher|steht|stehen|heute|morgen|hab|habe|"
        r"meine|meinem|bitte|gibt)\b",
        text,
        re.I,
    ):
        return "de"
    return "en"


def check_evidence_domain(
    text: str,
    *,
    enabled: bool,
    domains: Mapping[str, Sequence[str]],
    capability_registry: Any,
    domain_tool_map: Mapping[str, str],
    refusal_hint_fn: Callable[[str, str], str] | None = None,
) -> EvidenceVerdict:
    """Classify one utterance against the evidence-required domains."""
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

    lang = _detect_lang(t)
    base = (
        _REFUSAL_DE.get(matched_domain, _REFUSAL_DE_FALLBACK)
        if lang == "de"
        else _REFUSAL_EN.get(matched_domain, _REFUSAL_EN_FALLBACK)
    )
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


__all__ = ["EvidenceVerdict", "check_evidence_domain"]
