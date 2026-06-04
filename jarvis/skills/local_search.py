"""Local skill search + sidebar filter routing for ``POST /api/skills/query``.

This is the module the skills REST route imports. It deliberately does **not**
require SQLite FTS5 or an LLM: the installed skill set is small (tens of
entries), so a single in-memory pass with token scoring is both fast and
cloud-first (runs on a 1-vCPU VPS with no GPU, no native extension).

Contract expected by ``jarvis/ui/web/skills_routes.py``:

- ``LocalSearchFilters(q, category, state, risk, is_builtin, tags, limit)``
- ``LocalSkillSearch(registry=..., brain=...)`` exposing ``_registry`` and ``_brain``
- ``await searcher.query(filters) -> tuple[list[SkillHit], bool]`` where each hit
  carries ``.name`` / ``.score`` / ``.reason`` and the bool is ``brain_used``.

With an empty query the call acts as a pure filter router (category / state /
risk / builtin / tags). With a query it additionally token-scores name,
description, tags and category and drops non-matching skills.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

# Field weights for query token scoring. Name matches are the strongest signal,
# tags next, then category and free-text description.
_WEIGHT_NAME = 3.0
_WEIGHT_TAG = 2.0
_WEIGHT_CATEGORY = 1.0
_WEIGHT_DESCRIPTION = 1.0

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class LocalSearchFilters:
    """Filter set for a single skill query.

    ``tags`` is a tuple so the whole filter object stays hashable/frozen.
    A ``None`` filter means "do not constrain on this dimension".
    """

    q: str = ""
    category: str | None = None
    state: str | None = None
    risk: str | None = None
    is_builtin: bool | None = None
    tags: tuple[str, ...] = ()
    limit: int = 30


@dataclass(frozen=True)
class SkillHit:
    """A single matched skill, ranked by ``score`` (higher = better)."""

    name: str
    score: float
    reason: str


class LocalSkillSearch:
    """In-memory skill search over a ``SkillRegistry``.

    The instance is cached on ``app.state`` by the route and reused across
    requests; ``_registry`` lets the route detect a registry swap and ``_brain``
    is kept for API parity (an LLM rerank could hook in later but is not
    required — ``query`` never blocks on the network).
    """

    def __init__(self, registry: Any, brain: Any | None = None) -> None:
        self._registry = registry
        self._brain = brain

    def _is_builtin(self, name: str) -> bool:
        return name in BUILTIN_SKILL_NAMES

    def _passes_filters(self, skill: Any, f: LocalSearchFilters) -> bool:
        """Structural filters that do not depend on the free-text query."""
        if f.is_builtin is not None and self._is_builtin(skill.name) != f.is_builtin:
            return False
        if f.state is not None and skill.state.value != f.state:
            return False

        fm = getattr(skill, "frontmatter", None)
        # DRAFT / broken skills have no frontmatter — they can only satisfy a
        # frontmatter-independent filter (state / is_builtin handled above).
        if f.category is not None:
            if fm is None or fm.category != f.category:
                return False
        if f.tags:
            skill_tags = set(getattr(fm, "tags", []) or []) if fm else set()
            if not set(f.tags).issubset(skill_tags):
                return False
        if f.risk is not None:
            tier = None
            if fm is not None:
                tier = getattr(getattr(fm, "risk_policy", None), "default_tier", None)
            if tier != f.risk:
                return False
        return True

    def _score(self, skill: Any, query_tokens: list[str]) -> tuple[float, str]:
        """Token-overlap score + a short human-readable reason.

        Returns ``(0.0, "")`` when the query matches nothing in this skill so
        the caller can drop it.
        """
        fm = getattr(skill, "frontmatter", None)
        name_tokens = set(_tokens(skill.name))
        desc_tokens = set(_tokens(getattr(fm, "description", "") or "")) if fm else set()
        cat_tokens = set(_tokens(getattr(fm, "category", "") or "")) if fm else set()
        tag_tokens = (
            {t for tag in (getattr(fm, "tags", []) or []) for t in _tokens(tag)}
            if fm
            else set()
        )

        score = 0.0
        matched_fields: list[str] = []
        for tok in set(query_tokens):
            if tok in name_tokens:
                score += _WEIGHT_NAME
                matched_fields.append("name")
            if tok in tag_tokens:
                score += _WEIGHT_TAG
                matched_fields.append("tag")
            if tok in cat_tokens:
                score += _WEIGHT_CATEGORY
                matched_fields.append("category")
            if tok in desc_tokens:
                score += _WEIGHT_DESCRIPTION
                matched_fields.append("description")

        if score == 0.0:
            return 0.0, ""
        ordered = list(dict.fromkeys(matched_fields))  # de-dupe, keep order
        return score, "matched " + ", ".join(ordered)

    async def query(self, filters: LocalSearchFilters) -> tuple[list[SkillHit], bool]:
        """Filter + (optionally) rank the registry. Never raises on empty data."""
        skills = list(self._registry.list())
        query_tokens = _tokens(filters.q)

        hits: list[SkillHit] = []
        for skill in skills:
            if not self._passes_filters(skill, filters):
                continue
            if query_tokens:
                score, reason = self._score(skill, query_tokens)
                if score == 0.0:
                    continue
            else:
                score, reason = 0.0, "filter match"
            hits.append(SkillHit(name=skill.name, score=score, reason=reason))

        # Highest score first; stable tie-break by name for deterministic output.
        hits.sort(key=lambda h: (-h.score, h.name))
        if filters.limit and filters.limit > 0:
            hits = hits[: filters.limit]

        # No LLM rerank — keep the voice/UI path off the network (cloud-first).
        brain_used = False
        return hits, brain_used
