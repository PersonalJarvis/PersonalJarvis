"""Voice-UX-Layer für die Self-Mod-Pipeline (Phase 7.4+).

`jarvis/voice/` ist neu in Phase 7.4 angelegt — separate vom
`jarvis/speech/`-Pipeline-Stack. Begründung: die Echo-Confirmation
ist eine UX-Schicht, die zwischen Brain-Tool-Output und TTS sitzt;
keine direkte Audio-IO. Phase 7.6 wird einen Adapter zwischen
`jarvis.speech.pipeline.SpeechPipeline` und `SelfModFlowController`
einbauen.
"""
from __future__ import annotations

from .echo_confirmation import (
    classify_response,
    format_confirmation,
    format_outcome,
    is_sensitive_path,
)
from .self_mod_flow import (
    FlowSession,
    FlowState,
    SelfModFlowController,
)

__all__ = [
    "FlowSession",
    "FlowState",
    "SelfModFlowController",
    "classify_response",
    "format_confirmation",
    "format_outcome",
    "is_sensitive_path",
]
