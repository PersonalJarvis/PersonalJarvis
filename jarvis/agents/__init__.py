"""Sub-Agent registry — live tree of all active Jarvis sub-agents.

Subscribes to the EventBus and builds an in-memory tree from the Phase-5.5
instrumentation events (OpenClawTaskStarted/Completed, BrainTurnStarted/
Completed, ToolCallStarted/Completed, HarnessDispatched/Completed).
Consumed by the desktop app canvas view via the API
``GET /api/sub-agents/tree``.
"""
from .registry import AgentNode, SubAgentRegistry

__all__ = ["AgentNode", "SubAgentRegistry"]
