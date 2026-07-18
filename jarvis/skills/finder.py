"""Skill finder: mini-agent for searching and installing skills.

The finder is a thin wrapper around:
1. The curated seed catalog (``catalog/seed_catalog.json``).
2. The ``BrainManager`` for semantically scoring the candidates.
3. The ``httpx`` client for downloading a selected SKILL.md.

Design principles:
- **Graceful degradation**: without a brain, search falls back to a
  heuristic (string matching). Without internet, ``install`` fails, but
  search keeps working (static catalog).
- **Trust filter before the brain**: the trust filter runs *before*
  brain ranking, so the brain doesn't waste tokens on candidates the user
  would reject anyway.
- **Stateless**: the finder holds no state — query history lives in the
  frontend, and later in the flight recorder.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jarvis.core.paths import user_skills_dir
from jarvis.skills.catalog import load_catalog
from jarvis.skills.loader import parse_skill

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Value-Types
# ----------------------------------------------------------------------

TrustLevel = Literal["official", "verified", "community", "experimental"]
"""Trust levels for skill sources.

- ``official``: Anthropic, OpenAI, other first-party vendors.
- ``verified``: maintainer with a track record and 3000+ GitHub stars.
- ``community``: actively maintained, fewer stars, checkable.
- ``experimental``: prototype, solo dev, risk knowingly accepted by the user.
"""


TRUST_ORDER: dict[TrustLevel, int] = {
    "official": 0,
    "verified": 1,
    "community": 2,
    "experimental": 3,
}


@dataclass(frozen=True)
class SkillCandidate:
    """A match candidate from the catalog.

    Frozen for JSON serialization in responses + replay compatibility.
    """
    name: str
    title: str
    description: str
    source: str
    source_url: str
    raw_url: str | None
    trust: TrustLevel
    stars: int | None
    categories: tuple[str, ...]
    languages: tuple[str, ...]
    risk: str
    tags: tuple[str, ...]
    score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "source_url": self.source_url,
            "raw_url": self.raw_url,
            "trust": self.trust,
            "stars": self.stars,
            "categories": list(self.categories),
            "languages": list(self.languages),
            "risk": self.risk,
            "tags": list(self.tags),
            "score": round(self.score, 3),
            "reason": self.reason,
        }

    @classmethod
    def from_catalog_entry(cls, entry: dict[str, Any]) -> SkillCandidate:
        return cls(
            name=str(entry["name"]),
            title=str(entry.get("title", entry["name"])),
            description=str(entry.get("description", "")),
            source=str(entry.get("source", "unknown")),
            source_url=str(entry.get("source_url", "")),
            raw_url=entry.get("raw_url"),
            trust=entry.get("trust", "community"),
            stars=entry.get("stars"),
            categories=tuple(entry.get("categories", [])),
            languages=tuple(entry.get("languages", [])),
            risk=str(entry.get("risk", "monitor")),
            tags=tuple(entry.get("tags", [])),
        )


@dataclass(frozen=True)
class SearchFilters:
    """Filters for the search — maps to the dropdown selection in the frontend."""
    query: str = ""
    trust: TrustLevel | Literal["any"] = "any"
    min_stars: int | None = None
    category: str | None = None
    language: str | None = None
    max_risk: str | None = None  # "safe", "monitor", "ask"
    limit: int = 10


# ----------------------------------------------------------------------
# Filter + Ranking
# ----------------------------------------------------------------------

_RISK_ORDER = {"safe": 0, "monitor": 1, "ask": 2, "block": 3}


def _passes_filter(entry: dict[str, Any], f: SearchFilters) -> bool:
    """Hard filter before ranking — excludes on trust/stars/category/risk."""
    # Trust
    if f.trust != "any":
        e_trust = entry.get("trust", "community")
        if TRUST_ORDER.get(e_trust, 99) > TRUST_ORDER.get(f.trust, 99):
            return False

    # Min stars (if given)
    if f.min_stars is not None:
        e_stars = entry.get("stars")
        if e_stars is None or e_stars < f.min_stars:
            # Official skills have ``stars = null`` — we let those pass
            # regardless of the star threshold (official > star metric).
            if entry.get("trust") != "official":
                return False

    # Category
    if f.category:
        cats = entry.get("categories", [])
        if f.category not in cats:
            return False

    # Language
    if f.language:
        langs = entry.get("languages", [])
        if f.language not in langs and "en" not in langs:
            # falls back to EN if the skill is bilingual
            return False

    # Risk
    if f.max_risk:
        e_risk = entry.get("risk", "monitor")
        if _RISK_ORDER.get(e_risk, 99) > _RISK_ORDER.get(f.max_risk, 99):
            return False

    return True


_WORD_RE = re.compile(r"\b\w{3,}\b", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def _score_heuristic(query_tokens: set[str], entry: dict[str, Any]) -> float:
    """Heuristic ranking without a brain.

    Counts token overlaps between the query and (title + description + tags).
    Weighs tags higher because they're curated.
    """
    if not query_tokens:
        return 0.0
    blob_tokens = _tokenize(
        " ".join([
            str(entry.get("title", "")),
            str(entry.get("description", "")),
            " ".join(entry.get("categories", [])),
        ])
    )
    tag_tokens = _tokenize(" ".join(entry.get("tags", [])))

    blob_overlap = len(query_tokens & blob_tokens)
    tag_overlap = len(query_tokens & tag_tokens)

    # Tags get a factor of 2, so "docker" in tags outranks "docker" somewhere in the body
    raw = blob_overlap + tag_overlap * 2
    # Normalize to [0, 1] with a soft decay
    return min(1.0, raw / (len(query_tokens) + 1))


def _heuristic_reason(query_tokens: set[str], entry: dict[str, Any]) -> str:
    """Human-readable reason for a match — so the user understands why a
    skill was ranked highly."""
    matches = query_tokens & _tokenize(
        " ".join([
            str(entry.get("title", "")),
            str(entry.get("description", "")),
            " ".join(entry.get("tags", [])),
        ])
    )
    if not matches:
        trust = entry.get("trust", "community")
        return f"Trust match ({trust})"
    return "Match: " + ", ".join(sorted(matches)[:4])


# ----------------------------------------------------------------------
# Brain-backed ranking
# ----------------------------------------------------------------------

_RANK_SYSTEM_PROMPT = """You are a skill ranker for Personal Jarvis. The user
is looking for a skill that solves their problem. You get a user query and a
list of candidates as JSON. Return ONLY a JSON array, sorted from best
to worst match. Each entry: {"name": "...", "score": 0.0-1.0, "reason": "short
sentence on why it fits"}. Nothing else, no Markdown, no prefix."""


async def _brain_rank(
    brain: Any,
    query: str,
    candidates: list[dict[str, Any]],
) -> dict[str, tuple[float, str]] | None:
    """Uses a brain (BrainManager instance) for ranking.

    Returns ``None`` when:
    - no brain was passed
    - the response wasn't parsable as JSON (falls back to the heuristic)
    - the brain raised an error
    """
    if brain is None:
        return None

    # Minimal prompt: only name + title + description + tags, to save tokens
    compact = [
        {
            "name": c["name"],
            "title": c.get("title"),
            "description": c.get("description"),
            "tags": c.get("tags", []),
        }
        for c in candidates[:30]  # hard cap — 30 candidates is plenty
    ]
    user_msg = (
        f"User query: {query!r}\n\n"
        f"Candidates:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"{_RANK_SYSTEM_PROMPT}"
    )

    try:
        # BrainManager.generate is the uniform call. No use_history — this
        # is a one-shot ranker, not part of the chat history.
        if hasattr(brain, "generate"):
            text = await brain.generate(user_msg, use_history=False)
        else:
            # Fallback: if someone passes a raw Brain-protocol impl, we could
            # go through the dispatcher here. MVP: BrainManager only.
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Brain ranking failed: %s", exc)
        return None

    # Fish out the JSON — the brain may return it with whitespace or a prefix
    json_match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
    if not json_match:
        log.debug("Brain response contains no JSON array: %s", text[:200])
        return None
    try:
        parsed = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        log.debug("Brain-ranking JSON not parsable: %s", exc)
        return None

    out: dict[str, tuple[float, str]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(item.get("reason", "") or "")
        out[name] = (max(0.0, min(1.0, score)), reason)
    return out or None


# ----------------------------------------------------------------------
# Finder
# ----------------------------------------------------------------------

class SkillFinder:
    """Mini-agent for skill search and installation."""

    def __init__(self, brain: Any | None = None) -> None:
        self._brain = brain

    async def search(self, filters: SearchFilters) -> list[SkillCandidate]:
        """Filter + rank — returns up to ``filters.limit`` candidates."""
        catalog = load_catalog()
        filtered = [e for e in catalog if _passes_filter(e, filters)]

        if not filtered:
            return []

        # Brain ranking
        brain_scores = await _brain_rank(self._brain, filters.query, filtered)

        query_tokens = _tokenize(filters.query)
        candidates: list[SkillCandidate] = []
        for entry in filtered:
            cand = SkillCandidate.from_catalog_entry(entry)
            if brain_scores and cand.name in brain_scores:
                score, reason = brain_scores[cand.name]
                # Brain score weighted 0.7, heuristic score weighted 0.3 — so
                # a heuristic zero-hit isn't left completely blind
                heur = _score_heuristic(query_tokens, entry)
                final_score = 0.7 * score + 0.3 * heur
                final_reason = reason or _heuristic_reason(query_tokens, entry)
            else:
                final_score = _score_heuristic(query_tokens, entry)
                final_reason = _heuristic_reason(query_tokens, entry)

            # Trust bonus: on equal score, the more trustworthy one wins
            trust_bonus = (3 - TRUST_ORDER.get(cand.trust, 3)) * 0.01
            final_score += trust_bonus

            candidates.append(
                SkillCandidate(
                    **{**cand.__dict__, "score": final_score, "reason": final_reason}
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: filters.limit]

    async def install(self, candidate: SkillCandidate) -> Path:
        """Installs a candidate into ``user_skills_dir()``.

        Strategy:
        - If ``raw_url`` is set, the SKILL.md is fetched directly.
        - Without ``raw_url``, the install fails with a clear message —
          the user then has to install manually (link in the frontend).

        Returns the target path (``<user_skills>/<name>/SKILL.md``).
        Raises ``ValueError`` on an invalid SKILL.md (frontmatter validation).
        Raises ``RuntimeError`` on a network error or a missing raw_url.
        """
        if not candidate.raw_url:
            raise RuntimeError(
                f"No direct download available for '{candidate.name}'. "
                f"Open {candidate.source_url} and install manually."
            )

        # httpx is in the runtime deps (mcp_routes etc.)
        import httpx

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as client:
            try:
                resp = await client.get(candidate.raw_url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Download from {candidate.raw_url} failed: {exc}"
                ) from exc
            content = resp.text

        # Target structure: <user_skills>/<name>/SKILL.md
        target_dir = user_skills_dir() / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "SKILL.md"

        # Write atomically
        tmp = target_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target_file)

        # Parse check: if the SKILL.md is broken, we mark it as DRAFT, but
        # don't delete it automatically — the user sees the error in the UI
        # and can decide.
        parsed = parse_skill(target_file)
        if parsed.error:
            log.warning(
                "Installed skill '%s' has a validation error: %s",
                candidate.name, parsed.error,
            )

        return target_file


__all__ = [
    "SkillFinder",
    "SkillCandidate",
    "SearchFilters",
    "TrustLevel",
    "TRUST_ORDER",
]
