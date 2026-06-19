"""Phase-6 Critic loop — worker verification via out-of-process `openclaw agent`.

Re-exports of the public API. See submodules for implementation.
"""
from __future__ import annotations

from .escalation import choose_critic_model
from .log_summarizer import summarize_log
from .prompts import (
    ADVERSARIAL_REFRAME_PREFIX,
    CRITIC_SYSTEM_PROMPT,
    render_critic_prompt,
)
from .reflections import (
    Reflection,
    ReflectionMemory,
    reflections_path_for_mission,
    reflections_path_for_worker,
)
from .runner import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CRITIC_LOOPS,
    CriticRunner,
    build_critic_cmd,
)
from .verdict import (
    CRITIC_JSON_SCHEMA,
    LOW_CONFIDENCE_THRESHOLD,
    REQUIRED_AXES,
    CriticAxis,
    CriticIssue,
    CriticSchemaInvalid,
    CriticTimeout,
    CriticVerdict,
    CriticVerdictInconsistent,
    aggregate_axes_status,
    is_approval_valid,
    requires_escalation,
)


__all__ = [
    "ADVERSARIAL_REFRAME_PREFIX",
    "CRITIC_JSON_SCHEMA",
    "CRITIC_SYSTEM_PROMPT",
    "CriticAxis",
    "CriticIssue",
    "CriticRunner",
    "CriticSchemaInvalid",
    "CriticTimeout",
    "CriticVerdict",
    "CriticVerdictInconsistent",
    "DEFAULT_TIMEOUT_SECONDS",
    "LOW_CONFIDENCE_THRESHOLD",
    "MAX_CRITIC_LOOPS",
    "REQUIRED_AXES",
    "Reflection",
    "ReflectionMemory",
    "aggregate_axes_status",
    "build_critic_cmd",
    "choose_critic_model",
    "is_approval_valid",
    "reflections_path_for_mission",
    "reflections_path_for_worker",
    "render_critic_prompt",
    "requires_escalation",
    "summarize_log",
]
