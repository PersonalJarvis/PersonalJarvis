"""PrivacyFilter — Window-title- and process-based capture gating.

Three pattern sources (Plan §4 D-A1):
  1. SYSTEM (``jarvis.toml [awareness.privacy]``) — not user-editable
  2. USER (``preferences.toml [awareness.privacy]``) — additive via
     ``user_patterns_fn`` callback
  3. RUNTIME default (``block_for_browsers_allow_for_others``) — when
     no pattern source matches

Evaluation order in ``is_allowed``: BLOCK overrides ALLOW. Mirrors the
``_collect_patterns`` + ``fnmatch`` logic from
``jarvis.safety.risk_tier.RiskTierEvaluator``.

Reasons are machine-readable strings: ``matched_blocked_process:<pat>``,
``matched_blocked_title:<pat>``, ``matched_allowed_process:<pat>``,
``default_block_for_browser``, ``default_allow_for_unknown`` — they are
written to the ``AwarenessCaptureBlocked`` event and are grep-able in tests.
"""
from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable

from jarvis.awareness.config import AwarenessConfig

logger = logging.getLogger(__name__)

# Browser process names for the hybrid default. Lowercase for the
# case-insensitive comparison in is_allowed.
_BROWSER_NAMES: frozenset[str] = frozenset({
    "firefox.exe", "chrome.exe", "msedge.exe", "edge.exe",
    "opera.exe", "brave.exe", "arc.exe", "vivaldi.exe",
    "librewolf.exe", "iexplore.exe", "safari.exe",
})

# Callback signature for user_patterns_fn:
# returns (extra_blocked_processes, extra_blocked_title_patterns, extra_allowed_processes)
UserPatternsFn = Callable[[], "tuple[list[str], list[str], list[str]]"]


class PrivacyFilter:
    """Gatekeeper for awareness captures.

    Consulted in A1 before every FrameSnapshot insert. Frames with
    ``is_allowed=False`` are logged via the ``AwarenessCaptureBlocked`` event
    and are NOT emitted as ``FrameUpdated``.
    """

    def __init__(
        self,
        config: AwarenessConfig,
        *,
        user_patterns_fn: UserPatternsFn | None = None,
    ) -> None:
        self._privacy = config.privacy
        self._user_patterns_fn = user_patterns_fn

    def _collect_patterns(self, kind: str) -> list[str]:
        """Merges SYSTEM patterns with USER patterns.

        USER is strictly additive — cannot remove SYSTEM defaults.
        Mirrors ``RiskTierEvaluator._collect_patterns``.
        """
        if kind == "blocked_processes":
            patterns = list(self._privacy.blocked_processes)
        elif kind == "blocked_titles":
            patterns = list(self._privacy.blocked_title_patterns)
        elif kind == "allowed_processes":
            patterns = list(self._privacy.allowed_processes)
        else:
            return []

        if self._user_patterns_fn is None:
            return patterns

        try:
            extra_procs, extra_titles, extra_allowed = self._user_patterns_fn()
        except Exception:  # noqa: BLE001
            # A broken user-provider must not hang the privacy gate.
            # Pattern from risk_tier.RiskTierEvaluator._collect_patterns.
            logger.warning(
                "user_patterns_fn raised — using SYSTEM-defaults only",
                exc_info=True,
            )
            return patterns

        if kind == "blocked_processes":
            patterns.extend(extra_procs)
        elif kind == "blocked_titles":
            patterns.extend(extra_titles)
        elif kind == "allowed_processes":
            patterns.extend(extra_allowed)
        return patterns

    @staticmethod
    def _matches(value: str, pattern: str) -> bool:
        """Case-insensitive fnmatch — as in ``risk_tier.py:105``."""
        return fnmatch.fnmatchcase(value, pattern) or fnmatch.fnmatchcase(
            value.lower(), pattern.lower(),
        )

    def is_allowed(self, *, window_title: str, process_name: str) -> tuple[bool, str]:
        """Verdict for a frame. Returns (allowed, reason).

        ``reason`` is written to the ``AwarenessCaptureBlocked`` event (on BLOCK)
        or is used only for debug logging (on ALLOW). Evaluation order:
        Blocked Processes → Blocked Titles → Allowed Processes → Default Hybrid.
        """
        # 1. Blocked Processes — hardest block (password managers, banking apps)
        for pattern in self._collect_patterns("blocked_processes"):
            if self._matches(process_name, pattern):
                return False, f"matched_blocked_process:{pattern}"

        # 2. Blocked Titles — title heuristic (banking, password, incognito)
        for pattern in self._collect_patterns("blocked_titles"):
            if self._matches(window_title, pattern):
                return False, f"matched_blocked_title:{pattern}"

        # 3. Allowed Processes — coding apps explicitly permitted
        for pattern in self._collect_patterns("allowed_processes"):
            if self._matches(process_name, pattern):
                return True, f"matched_allowed_process:{pattern}"

        # 4. Default hybrid (D-A1: block_for_browsers_allow_for_others).
        # Browsers have high PII risk (URLs/tabs), unknown apps generally
        # low risk (productivity tools). Additional default strategies
        # can be added here if made configurable.
        if self._privacy.default_when_unknown == "block_for_browsers_allow_for_others":
            if process_name.lower() in _BROWSER_NAMES:
                return False, "default_block_for_browser"
            return True, "default_allow_for_unknown"
        return True, "default_allow_for_unknown"
