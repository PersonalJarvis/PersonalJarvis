"""``computer_use`` — router-tier tool for live-desktop control (Wave 1).

This is the first-class, clearly-described entry point the router brain uses to
drive the user's *actual* machine: open apps, click, type, scroll, drag — every
mouse-and-keyboard action. It exists because the router previously had no honest
path for desktop actions:

* ``spawn_worker`` runs a worker in an isolated git worktree — it can edit code
  and research, but it can **never** touch the user's live desktop.
* ``dispatch_to_harness`` could reach the computer-use harness, but only through
  a two-level indirection (``harness="computer-use"``) whose schema description
  talks about "OpenClaw, Codex, code-editing, research" — so the model never
  picked it for desktop actions and instead refused or hallucinated a tool.

A dedicated tool with an unambiguous name + description is the strongest signal
for LLM tool selection. Execution is delegated to the canonical in-process
``computer-use`` harness (``jarvis/plugins/harness/computer_use.py`` →
``jarvis/harness/screenshot_only_loop.py``), which gates every individual action
through the ToolExecutor risk tiers (ADR-0008). The tool itself is a direct,
safe-gated action — never a spawn — so it carries no D9 recursion risk and never
belongs in a worker tool set (AP-5/AP-14).
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.local_action_gate import HARNESS_NAME
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.harness.manager import HarnessManager
from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool

#: Default ceiling for a single computer-use run, in seconds. A multi-step GUI
#: loop (open app → screenshot → click/type → verify) needs a generous budget;
#: the per-step timeout inside the loop bounds individual actions.
_DEFAULT_TIMEOUT_S = 120.0


class ComputerUseTool:
    """Drive the live desktop via the in-process computer-use harness."""

    name: str = "computer_use"
    risk_tier: str = "monitor"
    description: str = (
        "Control THIS computer's live desktop with mouse and keyboard: open "
        "apps, click buttons, type into fields, scroll, navigate the screen, "
        "operate any GUI application. Use this for ANY on-screen / app / "
        "desktop action the user asks for (e.g. 'open a terminal', 'open Chrome "
        "and go to gmail', 'click the blue button', 'type X into Notepad', "
        "'scroll down'). Pass the user's request verbatim as 'goal'. This drives "
        "the real machine — unlike spawn_worker, which only works in an "
        "isolated code workspace and cannot touch the desktop."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "The desktop action to perform, in the user's own words "
                    "(e.g. 'open a terminal and start the cloud config')."
                ),
            },
        },
        "required": ["goal"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        manager: HarnessManager | None = None,
        max_output_chars: int = 4000,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        # Reuse the harness-dispatch plumbing (streaming, trimming, timeout)
        # rather than re-implementing it; we only fix the harness identity.
        self._dispatch = DispatchToHarnessTool(
            bus=bus,
            manager=manager,
            max_output_chars=max_output_chars,
        )
        self._timeout_s = float(timeout_s)

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        goal = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(success=False, output=None, error="goal missing")
        return await self._dispatch.execute(
            {
                "harness": HARNESS_NAME,
                "prompt": goal,
                "timeout_s": self._timeout_s,
            },
            ctx,
        )
