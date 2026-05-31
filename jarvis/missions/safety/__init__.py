"""Phase-6 safety layer — injection scanner, path guard, destructive confirm.

Re-exports of the public API. See submodules for implementation.

Foundation: ADR-0009 §"Risk Register Top 10" items #5 (hallucinated execution),
#7 (prompt injection via tool output) and Decision §"Voice confirm for
destructive patterns".
"""
from __future__ import annotations

from .destructive_confirm import (
    DESTRUCTIVE_PATTERNS,
    DestructiveDetection,
    is_destructive,
)
from .injection_scanner import (
    INJECTION_PATTERNS,
    InjectionDetection,
    InjectionSeverity,
    InjectionWhere,
    has_high_severity,
    scan,
)
from .path_guard import (
    DEFAULT_BLOCKED_GLOBS,
    check_prompt_for_blocked_paths,
    filter_diff_paths,
    is_blocked,
)

__all__ = [
    "DEFAULT_BLOCKED_GLOBS",
    "DESTRUCTIVE_PATTERNS",
    "DestructiveDetection",
    "INJECTION_PATTERNS",
    "InjectionDetection",
    "InjectionSeverity",
    "InjectionWhere",
    "check_prompt_for_blocked_paths",
    "filter_diff_paths",
    "has_high_severity",
    "is_blocked",
    "is_destructive",
    "scan",
]
