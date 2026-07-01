"""Risk-tier evaluation: blacklist > whitelist > tool default.

The priority order matters and is non-negotiable:

1. **Blacklist** beats everything — a match raises an `ActionBlocked` exception.
2. **Whitelist** downgrades the tier to `safe` + `approved_by="whitelist"`,
   even if the tool itself declares the `ask` tier. This is the concrete
   solution for "anti-confirmation-fatigue" (user preference).
3. **Tool default**, or fallback to `config.safety.default_tier`.

Matching runs via an `fnmatch` glob against `"<tool_name> <serialized_args>"`.
"""
from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_args

from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import RiskTier, Tool

log = logging.getLogger(__name__)

#: Valid RiskTier vocabulary, derived from the Literal (drift-proof) so a hook
#: return value or a misconfigured static tier is validated before it reaches
#: the always_block / always_confirm membership checks. An unvalidated string
#: would miss both checks and behave as the most permissive option silently.
_VALID_TIERS: frozenset[str] = frozenset(get_args(RiskTier))

# Callable that returns (whitelist_patterns, blacklist_patterns). Called on every
# evaluate() call — keep it cheap. Current user: CliToolRegistry flattens spec
# patterns per connected CLI into this tuple, so that
# ``gcloud * delete *`` gets blocked without an entry in jarvis.toml.
ExtraPatternsFn = Callable[[], "tuple[list[str], list[str]]"]


class ActionBlocked(Exception):
    """Raised when a tool call matches the blacklist."""

    def __init__(self, pattern: str, matched: str) -> None:
        super().__init__(f"Blacklist match: '{matched}' blocked by pattern '{pattern}'")
        self.pattern = pattern
        self.matched = matched


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Result of a tier evaluation."""
    tier: RiskTier
    approved_by: str | None   # "whitelist" | None
    matched_pattern: str | None
    command_string: str


def _serialize_args(args: dict[str, Any]) -> str:
    """Serializes args into a flat string for pattern matching.

    For matching we only use string values + numbers; dict/list values
    are stringified with `str()`, but blacklist/whitelist should
    primarily match `run_shell` commands anyway (those commands are flat).
    """
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{v}")
        else:
            parts.append(f"{k}={v!r}")
    return " ".join(parts)


class RiskTierEvaluator:
    """Evaluator with a config snapshot (whitelist/blacklist patterns).

    Optional: an ``extra_patterns_fn`` supplies additional patterns that are
    freshly queried on every ``evaluate()`` call. This lets the CLI
    integration mix spec patterns from the catalog into the safety gates per
    connected CLI, without touching jarvis.toml. Errors from
    ``extra_patterns_fn`` are swallowed — a broken catalog must never hang
    the safety gate.
    """

    def __init__(
        self,
        safety: SafetyConfig,
        *,
        extra_patterns_fn: ExtraPatternsFn | None = None,
    ) -> None:
        self._safety = safety
        self._extra_patterns_fn = extra_patterns_fn

    def _collect_patterns(self, kind: str) -> list[str]:
        """Merges system patterns (jarvis.toml) with extra patterns (e.g. CLI specs)."""
        if kind == "whitelist":
            patterns = list(self._safety.whitelist.commands)
        elif kind == "blacklist":
            patterns = list(self._safety.blacklist.commands)
        else:
            patterns = []
        if self._extra_patterns_fn is not None:
            try:
                whitelist, blacklist = self._extra_patterns_fn()
                patterns.extend(whitelist if kind == "whitelist" else blacklist)
            except Exception:  # noqa: BLE001
                pass
        return patterns

    def evaluate(self, tool: Tool, args: dict[str, Any]) -> TierDecision:
        cmd = f"{tool.name} {_serialize_args(args)}".strip()

        # 1. Blacklist — hard block
        for pattern in self._collect_patterns("blacklist"):
            if fnmatch.fnmatchcase(cmd, pattern) or fnmatch.fnmatchcase(cmd.lower(), pattern.lower()):
                raise ActionBlocked(pattern=pattern, matched=cmd)

        # 2. Whitelist — downgrade to safe
        for pattern in self._collect_patterns("whitelist"):
            if fnmatch.fnmatchcase(cmd, pattern) or fnmatch.fnmatchcase(cmd.lower(), pattern.lower()):
                return TierDecision(
                    tier="safe",
                    approved_by="whitelist",
                    matched_pattern=pattern,
                    command_string=cmd,
                )

        # 3. Tool default (fallback to the config default), refined by an
        # optional per-action hook. A tool with mixed actions (e.g. gmail: list/get
        # read, send consequential) may expose ``risk_tier_for_args`` so a read
        # call is not forced through the same ask-confirm as a send (forensic
        # 2026-06-19, session dc533e39). The hook refines ONLY the tool default
        # — blacklist and whitelist (above) keep priority. Every value (static
        # or hook) is validated against the RiskTier vocabulary: an invalid
        # string must never flow into the always_block / always_confirm
        # membership checks (it would silently act as the most permissive
        # option). A broken hook must never crash the gate.
        raw_tier = getattr(tool, "risk_tier", None)
        tier: RiskTier = raw_tier if raw_tier in _VALID_TIERS else self._safety.default_tier
        hook = getattr(tool, "risk_tier_for_args", None)
        if callable(hook):
            try:
                dynamic = hook(args)
            except Exception as exc:  # noqa: BLE001 — a broken hook must not break the gate
                log.warning(
                    "risk_tier_for_args on %r raised %r — falling back to static tier %r",
                    tool.name, exc, tier,
                )
                dynamic = None
            if dynamic in _VALID_TIERS:
                tier = dynamic
            elif dynamic is not None:
                log.warning(
                    "risk_tier_for_args on %r returned unknown tier %r — "
                    "falling back to static tier %r",
                    tool.name, dynamic, tier,
                )

        # block tier gets bounced directly
        if tier in self._safety.always_block_tiers:
            raise ActionBlocked(pattern="<tool-declared-block>", matched=cmd)

        return TierDecision(
            tier=tier,
            approved_by=None,
            matched_pattern=None,
            command_string=cmd,
        )

    def needs_user_confirmation(self, decision: TierDecision) -> bool:
        """True if a user confirmation must be obtained before execution."""
        if decision.approved_by is not None:
            return False
        return decision.tier in self._safety.always_confirm_tiers
