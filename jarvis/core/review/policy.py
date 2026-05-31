"""ReviewPolicy: classifier "review or not" (Phase 8.4).

Plan reference: §AD-6 (selective activation — naive reviewers on
simple tasks lower the pass rate). The main Jarvis uses this heuristic
as a decision aid; the tool `dispatch_with_review` is the only switch
point that actually activates it. ReviewPolicy is NOT a gate.

Hard rule: no LLM calls. Pure pattern match on the task string,
synchronous, < 1ms. Latency is at a premium on the main Jarvis path
(plan §3 Phase 5 — sub-second first token).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Action keywords: tasks with these verbs/nouns typically target code
# generation, file mutation, or multi-step synthesis and justify a quality gate.
_DEFAULT_REVIEW_KEYWORDS: tuple[str, ...] = (
    "schreib",
    "generier",
    "erstelle",
    "code",
    "script",
    "skill",
    "datei",
    "modifizier",
    "refactor",
)

# Smalltalk allowlist (plan-§AD-6): conversation NEVER runs through the
# pipeline.
_DEFAULT_NOREVIEW_KEYWORDS: tuple[str, ...] = (
    "hi",
    "hallo",
    "danke",
    "was ist",
    "wer ist",
    "wie spät",
    "wie spaet",
)

_DEFAULT_MIN_LENGTH = 30


@dataclass
class PolicyDecision:
    """Classification result with reason for audit/trace."""

    should_review: bool
    reason: str


@dataclass
class ReviewPolicy:
    """Heuristic classification. Phase 8.2 delivered the stub, Phase 8.4
    the full pattern matcher.

    Conservative default: if neither action keyword nor smalltalk pattern
    matches, the default is `False`. Plan §AD-6: reviewers on simple tasks
    lower the pass rate; better to review too little than too much.
    """

    review_keywords: tuple[str, ...] = field(
        default_factory=lambda: _DEFAULT_REVIEW_KEYWORDS
    )
    noreview_keywords: tuple[str, ...] = field(
        default_factory=lambda: _DEFAULT_NOREVIEW_KEYWORDS
    )
    min_length: int = _DEFAULT_MIN_LENGTH

    def __post_init__(self) -> None:
        # Word-boundary regex prevents `code` from matching inside `decode`
        # or `hi` inside `historisch`.
        self._review_re = re.compile(
            r"\b(?:" + "|".join(re.escape(k) for k in self.review_keywords) + r")",
            re.IGNORECASE,
        )
        self._noreview_re = re.compile(
            r"\b(?:" + "|".join(re.escape(k) for k in self.noreview_keywords) + r")\b",
            re.IGNORECASE,
        )

    def should_review(
        self, task: str, *, context: dict[str, Any] | None = None
    ) -> PolicyDecision:
        """Classifies `task` as "review" / "no_review" with a reason.

        Rule evaluation order:
        1. Length filter (< min_length → no_review).
        2. Smalltalk keyword match → no_review.
        3. Action keyword match → review.
        4. Default → no_review (plan-§AD-6 conservative).
        """
        del context  # Phase 8.4 reserved parameter, unused in 8.4
        stripped = (task or "").strip()
        if len(stripped) < self.min_length:
            return PolicyDecision(
                should_review=False,
                reason=f"task too short ({len(stripped)} < {self.min_length})",
            )

        if self._noreview_re.search(stripped):
            return PolicyDecision(
                should_review=False,
                reason="smalltalk pattern matched",
            )

        if self._review_re.search(stripped):
            return PolicyDecision(
                should_review=True,
                reason="action keyword matched",
            )

        return PolicyDecision(
            should_review=False,
            reason="no review-trigger pattern matched (default conservative)",
        )
