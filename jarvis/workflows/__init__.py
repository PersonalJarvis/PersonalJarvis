"""Workflow system (Phase 6) — AI-agent orchestration dashboard.

A **workflow** is a named, reusable sequence of steps.
Separate from ``jarvis.tasks`` (single-action, persistent task queue)
and ``jarvis.skills`` (voice/hotkey-triggered SKILL.md files):

- Tasks = "run once, then gone" (reminders, ad hoc).
- Skills = natural-language shortcuts (voice-triggered macros).
- **Workflows = hosted multi-step pipelines** with a cron or manual
  trigger that chain brain prompts, harness calls, tool calls, and TTS
  announcements into a sequence. This is the AI-agent answer to n8n.

Belongs in Layer L6 (Orchestrator) per Plan §3.
"""
from __future__ import annotations

from .runner import WorkflowRunner
from .scheduler import WorkflowScheduler
from .schema import (
    BrainPromptStep,
    HarnessDispatchStep,
    ShellCmdStep,
    SpeakStep,
    TelegramSendStep,
    ToolCallStep,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunState,
    WorkflowStep,
    WorkflowTrigger,
)
from .seed import SEED_WORKFLOWS, ensure_seed_workflows
from .store import WorkflowStore

__all__ = [
    "BrainPromptStep",
    "HarnessDispatchStep",
    "ShellCmdStep",
    "SpeakStep",
    "TelegramSendStep",
    "ToolCallStep",
    "WorkflowDef",
    "WorkflowRun",
    "WorkflowRunState",
    "WorkflowRunner",
    "WorkflowScheduler",
    "WorkflowStep",
    "WorkflowStore",
    "WorkflowTrigger",
    "SEED_WORKFLOWS",
    "ensure_seed_workflows",
]
