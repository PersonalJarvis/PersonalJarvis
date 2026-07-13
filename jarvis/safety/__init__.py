"""Lazy public exports for the safety layer.

Keeping the package initializer import-free prevents an import-order cycle:
``approval -> safety.__init__ -> risk_tier -> config -> brain -> dispatcher ->
tool_executor -> risk_tier``. Concrete modules load only when their public name
is requested, while ``from jarvis.safety import ToolExecutor`` remains stable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .approval import ApprovalWorkflow
    from .risk_tier import ActionBlocked, RiskTierEvaluator, TierDecision
    from .tool_executor import ToolExecutor

__all__ = [
    "ActionBlocked",
    "ApprovalWorkflow",
    "RiskTierEvaluator",
    "TierDecision",
    "ToolExecutor",
]


def __getattr__(name: str) -> Any:
    if name == "ApprovalWorkflow":
        from .approval import ApprovalWorkflow

        return ApprovalWorkflow
    if name in {"ActionBlocked", "RiskTierEvaluator", "TierDecision"}:
        from .risk_tier import ActionBlocked, RiskTierEvaluator, TierDecision

        return {
            "ActionBlocked": ActionBlocked,
            "RiskTierEvaluator": RiskTierEvaluator,
            "TierDecision": TierDecision,
        }[name]
    if name == "ToolExecutor":
        from .tool_executor import ToolExecutor

        return ToolExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
