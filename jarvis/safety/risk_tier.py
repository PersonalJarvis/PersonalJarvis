"""Risk-Tier-Evaluation: Blacklist > Whitelist > Tool-Default.

Die Priorität ist wichtig und nicht verhandelbar:

1. **Blacklist** trumpft alles — Match → `ActionBlocked` Exception.
2. **Whitelist** downgraded Tier zu `safe` + `approved_by="whitelist"`.
   Auch wenn das Tool selbst `ask`-Tier deklariert. Das ist die konkrete
   Lösung für "Anti-Confirmation-Fatigue" (User-Pref).
3. **Tool-Default** oder Fallback auf `config.safety.default_tier`.

Matching läuft per `fnmatch`-Glob gegen `"<tool_name> <serialized_args>"`.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import RiskTier, Tool

# Callable that returns (whitelist_patterns, blacklist_patterns). Wird bei jedem
# evaluate() aufgerufen — billig halten. Aktueller Nutzer: CliToolRegistry
# flattened Spec-Patterns pro verbundener CLI in dieses Tupel, damit
# ``gcloud * delete *`` geblockt wird ohne Eintrag in jarvis.toml.
ExtraPatternsFn = Callable[[], "tuple[list[str], list[str]]"]


class ActionBlocked(Exception):
    """Wird geworfen wenn ein Tool-Call gegen die Blacklist matched."""

    def __init__(self, pattern: str, matched: str) -> None:
        super().__init__(f"Blacklist-Match: '{matched}' blockiert durch Pattern '{pattern}'")
        self.pattern = pattern
        self.matched = matched


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Ergebnis einer Tier-Evaluation."""
    tier: RiskTier
    approved_by: str | None   # "whitelist" | None
    matched_pattern: str | None
    command_string: str


def _serialize_args(args: dict[str, Any]) -> str:
    """Serialisiert Args zu einem flat-String für Pattern-Matching.

    Wir nutzen für Matching nur String-Values + Zahlen; Dict/List-Werte
    werden mit `str()` gekennzeichnet, aber Blacklist/Whitelist sollte
    ohnehin primär `run_shell`-Commands matchen (die Commands sind flach).
    """
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{v}")
        else:
            parts.append(f"{k}={v!r}")
    return " ".join(parts)


class RiskTierEvaluator:
    """Evaluator mit Config-Snapshot (Whitelist/Blacklist Patterns).

    Optional: ein ``extra_patterns_fn`` liefert zusaetzliche Patterns, die bei
    jedem ``evaluate()``-Call frisch abgefragt werden. Das ermoeglicht der
    CLI-Integration, pro verbundener CLI Spec-Patterns aus dem Katalog in die
    Safety-Gates zu mischen, ohne jarvis.toml zu beruehren. Fehler aus
    ``extra_patterns_fn`` werden geschluckt — ein kaputter Katalog darf das
    Safety-Gate nicht haengen lassen.
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
        """Mergt System-Patterns (jarvis.toml) mit Extra-Patterns (z.B. CLI-Specs)."""
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

        # 1. Blacklist — hartes Block
        for pattern in self._collect_patterns("blacklist"):
            if fnmatch.fnmatchcase(cmd, pattern) or fnmatch.fnmatchcase(cmd.lower(), pattern.lower()):
                raise ActionBlocked(pattern=pattern, matched=cmd)

        # 2. Whitelist — Downgrade zu safe
        for pattern in self._collect_patterns("whitelist"):
            if fnmatch.fnmatchcase(cmd, pattern) or fnmatch.fnmatchcase(cmd.lower(), pattern.lower()):
                return TierDecision(
                    tier="safe",
                    approved_by="whitelist",
                    matched_pattern=pattern,
                    command_string=cmd,
                )

        # 3. Tool-Default (fallback auf Config-Default)
        tier: RiskTier = tool.risk_tier or self._safety.default_tier

        # block-Tier wird direkt gebounced
        if tier in self._safety.always_block_tiers:
            raise ActionBlocked(pattern="<tool-declared-block>", matched=cmd)

        return TierDecision(
            tier=tier,
            approved_by=None,
            matched_pattern=None,
            command_string=cmd,
        )

    def needs_user_confirmation(self, decision: TierDecision) -> bool:
        """True wenn vor Execution eine User-Bestätigung einzuholen ist."""
        if decision.approved_by is not None:
            return False
        return decision.tier in self._safety.always_confirm_tiers
