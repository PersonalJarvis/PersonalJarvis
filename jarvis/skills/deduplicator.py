"""Skill deduplicator: finds similar skills via the Jaccard coefficient.

Feature set per skill: trigger patterns ∪ requires_tools.
"""
from __future__ import annotations

from .schema import Skill


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard coefficient |A∩B| / |A∪B|. Both empty → 1.0 (by definition)."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _features(skill: Skill) -> set[str]:
    feats: set[str] = set()
    if skill.frontmatter is None:
        return feats
    for t in skill.frontmatter.triggers:
        token = t.pattern or t.combo or t.cron
        if token:
            feats.add(f"{t.type}:{token.lower()}")
    for tool in skill.frontmatter.requires_tools:
        feats.add(f"tool:{tool.lower()}")
    return feats


def find_duplicates(
    skills: list[Skill],
    threshold: float = 0.75,
) -> list[tuple[Skill, Skill, float]]:
    """Returns pairs (a, b, similarity) that are ≥ threshold.

    Deterministically sorted (by descending similarity).
    """
    results: list[tuple[Skill, Skill, float]] = []
    feats = [(s, _features(s)) for s in skills]
    for i in range(len(feats)):
        a, fa = feats[i]
        for j in range(i + 1, len(feats)):
            b, fb = feats[j]
            score = jaccard(fa, fb)
            if score >= threshold:
                results.append((a, b, score))
    results.sort(key=lambda t: t[2], reverse=True)
    return results
