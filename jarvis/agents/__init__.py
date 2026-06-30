"""Jarvis-Agent registry — live tree of all active Jarvis-Agents.

Subscribes to the EventBus and builds an in-memory tree from the Phase-5.5
instrumentation events (JarvisAgentTaskStarted/Completed, BrainTurnStarted/
Completed, ToolCallStarted/Completed, HarnessDispatched/Completed).
Consumed by the desktop app canvas view via the API
``GET /api/sub-agents/tree``.
"""
from .registry import AgentNode, JarvisAgentRegistry

__all__ = ["AgentNode", "JarvisAgentRegistry"]
