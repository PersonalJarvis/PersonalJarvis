"""Curator — intelligent extraction + validation + merge into the workspace.

Workflow per turn:

    user_text, assistant_text
        │
        ▼
    Extractor  — LLM call with strict JSON schema
        │ candidates: [{subject, cluster, field, value, confidence, evidence}]
        ▼
    Validator  — subject disambiguation, confidence gates, contradiction check
        │ accepted: [...], rejected: [...], review: [...]
        ▼
    Merger     — atomic writes to USER.md / people/<name>.md / SOUL.md
        │ emits ProfileUpdated events
        ▼
    EventBus   — UI shows "3 new facts, 1 review"
"""
from __future__ import annotations

from .curator import Curator
from .extractor import Candidate, Extractor
from .merger import Merger
from .validator import ValidationResult, Validator

__all__ = [
    "Candidate",
    "Curator",
    "Extractor",
    "Merger",
    "ValidationResult",
    "Validator",
]
