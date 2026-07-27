"""Multi-agent workspace launcher ("Make It Yours").

A self-contained feature that opens N coding-agent terminals (Claude Code or
Codex) **inside the Jarvis desktop app** as an xterm grid — each running in the
Personal Jarvis project folder so the user can have those agents extend and
personalize Jarvis itself.

Design notes:
- The two agents are defined LOCALLY here (``agents.py``) rather than added to
  the shared CLI catalog. Catalog registration would expose ``cli_claude`` /
  ``cli_codex`` as brain-callable tools (a D9 recursion surface, AP-5/AP-14)
  and is not what this feature needs — we only detect, install, and launch.
  We still reuse the proven ``CliStatusProber`` machinery.
- Terminals are **embedded** (xterm.js panes driven by the workspace PTY
  WebSocket, ``jarvis/ui/web/workspace_routes.py``), not separate OS windows.
  A PTY is a kernel feature, so this works on a headless Linux VPS too.
- The "Do you trust this folder?" prompt is skipped by pre-seeding each CLI's
  own trust config for the project path (``trust.py``).
"""
from __future__ import annotations

from .agents import (
    AGENT_NAMES,
    AgentInfo,
    build_agent_argv,
    build_install_argv,
    detect_agents,
    get_agent,
    list_agents,
    pty_available,
)
from .launcher import LAYOUT_CHOICES, LaunchPlan, Slot, plan_workspace, validate_split

__all__ = [
    "AGENT_NAMES",
    "AgentInfo",
    "build_agent_argv",
    "build_install_argv",
    "detect_agents",
    "get_agent",
    "list_agents",
    "pty_available",
    "LAYOUT_CHOICES",
    "LaunchPlan",
    "Slot",
    "plan_workspace",
    "validate_split",
]
