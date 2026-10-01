"""Team-local Bayesian evidence accounting; never an authorization grant.

The prior is Beta(2,2). Difficulty 1/2 positive evidence saturates at 2/6
credits per domain; difficulty 3/4/5 earns 2/4/8 credits. Negative evidence
adds at most two beta observations and removes at most two level credits.
Thus unlimited trivial work cannot outweigh one verified difficult task.
"""

from __future__ import annotations

import math
from typing import Any

LEVEL_THRESHOLDS = (3, 8, 16, 32, 64, 128, 256, 512)


def initial_profile() -> dict[str, Any]:
    return {
        "alpha": 2.0,
        "beta": 2.0,
        "credits": 0.0,
        "verified_tasks": "0",
        "rejected_tasks": "0",
        "regressions": "0",
        "fabrications": "0",
        "difficulty_counts": {str(difficulty): "0" for difficulty in range(1, 6)},
        "level": 1,
        "reliability": 0.5,
        "uncertainty": math.sqrt(0.05),
        "samples": "0",
        "prior_alpha": 2,
        "prior_beta": 2,
    }


def observe(
    previous: dict[str, Any] | None,
    *,
    difficulty: int,
    accepted: bool,
    evidence_quality: float = 1.0,
    reason: str = "accepted",
) -> tuple[dict[str, Any], float]:
    """Return a new profile and auditable bounded credit delta."""
    if difficulty not in range(1, 6) or not 0.0 < evidence_quality <= 1.0:
        raise ValueError("Invalid difficulty or evidence quality")
    profile = dict(previous or initial_profile())
    counts = dict(profile["difficulty_counts"])
    count = int(counts[str(difficulty)])
    if accepted:
        if difficulty <= 2:
            ceiling = 2.0 if difficulty == 1 else 6.0
            weight = ceiling / ((count + 1) * (count + 2))
        else:
            weight = float(2 ** (difficulty - 2))
        delta = weight * evidence_quality
        profile["alpha"] += delta
        profile["verified_tasks"] = str(int(profile["verified_tasks"]) + 1)
        counts[str(difficulty)] = str(count + 1)
    else:
        weight = min(2.0, max(0.5, difficulty / 2)) * evidence_quality
        profile["beta"] += weight
        if reason not in {"regression", "fabrication"}:
            profile["rejected_tasks"] = str(int(profile["rejected_tasks"]) + 1)
        if reason in {"regression", "fabrication"}:
            key = "regressions" if reason == "regression" else "fabrications"
            profile[key] = str(int(profile[key]) + 1)
        delta = -min(profile["credits"], weight)
    profile["credits"] = max(0.0, profile["credits"] + delta)
    profile["difficulty_counts"] = counts
    profile["samples"] = str(int(profile["samples"]) + 1)
    alpha, beta = profile["alpha"], profile["beta"]
    profile["reliability"] = alpha / (alpha + beta)
    profile["uncertainty"] = math.sqrt(alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1)))
    profile["level"] = 1 + sum(profile["credits"] >= threshold for threshold in LEVEL_THRESHOLDS)
    return profile, delta
