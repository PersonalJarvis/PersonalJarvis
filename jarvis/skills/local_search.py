"""Local skill search + sidebar filter routing for ``POST /api/skills/query``.

This is the module the skills REST route imports. It requires no SQLite FTS5 and
no LLM: the installed skill set is small, so ranking happens in memory and the
call never blocks on the network (it runs on a 1-vCPU VPS with no GPU and no
native extension).

**It no longer owns a scoring function.** Ranking is delegated to
:mod:`jarvis.skills.relevance`, the same scorer the voice path uses. That is the
whole point of this module's current shape: two independent scorers over the same
corpus drift, and the drift is invisible — the UI search box would keep finding
skills the assistant cannot, or the reverse, with nothing to catch it. A parity
test pins the two surfaces to identical scores.

The old local scorer also demonstrated the cost concretely: it weighted only
name, tags, category and description, so it had no channel for trigger literals,
``when_to_use`` or ``intent_objects``. Searching the box for a German trigger
word a skill genuinely declares found nothing.

Two deliberate differences remain between the surfaces, and both are structural
rather than incidental:

* The UI index includes INACTIVE skills — you must be able to find a draft in
  order to promote it. The voice index does not (AP-15). One boolean, applied at
  index-BUILD time rather than filtered at query time, so a draft is absent from
  the voice vocabulary entirely instead of relying on a later check.
* The structural filters below (category / state / risk / builtin / tags) are
  UI-only sidebar concerns and have no business in the voice scorer.

Contract expected by ``jarvis/ui/web/skills_routes.py``:

- ``LocalSearchFilters(q, category, state, risk, is_builtin, tags, limit)``
- ``LocalSkillSearch(registry=..., brain=...)`` exposing ``_registry`` and ``_brain``
- ``await searcher.query(filters) -> tuple[list[SkillHit], bool]`` where each hit
  carries ``.name`` / ``.score`` / ``.reason`` and the bool is ``brain_used``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis.skills.builtin import BUILTIN_SKILL_NAMES
from jarvis.skills.relevance import build_index


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

    async def query(self, filters: LocalSearchFilters) -> tuple[list[SkillHit], bool]:
        """Filter, then rank with the shared scorer. Never raises on empty data."""
        try:
            skills = list(self._registry.list())
        except Exception:  # noqa: BLE001 — an empty registry is not an error
            skills = []

        candidates = [s for s in skills if self._passes_filters(s, filters)]
        if not filters.q.strip():
            # Pure filter routing for the sidebar: no query, no ranking, so the
            # order is deterministic by name.
            hits = [
                SkillHit(name=s.name, score=0.0, reason="filter match")
                for s in sorted(candidates, key=lambda s: s.name.lower())
            ]
            return self._limit(hits, filters), False

        # Build over the FILTERED set so IDF reflects what the user is actually
        # searching in — narrowing to one category should make that category's
        # shared vocabulary less distinctive, not more.
        index = build_index(candidates)
        ranking = index.rank(filters.q, limit=max(1, filters.limit or 30))
        hits = [
            SkillHit(
                name=scored.name,
                score=round(scored.score, 6),
                reason=scored.reason or "matched",
            )
            for scored in ranking.ranked
        ]

        # No LLM rerank — keep the voice/UI path off the network (cloud-first).
        return self._limit(hits, filters), False

    @staticmethod
    def _limit(hits: list[SkillHit], filters: LocalSearchFilters) -> list[SkillHit]:
        if filters.limit and filters.limit > 0:
            return hits[: filters.limit]
        return hits


__all__ = ["LocalSearchFilters", "LocalSkillSearch", "SkillHit"]
