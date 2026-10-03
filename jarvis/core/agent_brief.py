"""How Jarvis words a task it hands to another agent.

Every hand-off surface (workspace coding panes, the agent society, the live
voice backends) writes its own brief, and none of them said what that brief
should ask for. Left alone, the model read "check", "look into", "deep dive"
or "audit" as a request for a report and wrote "READ-ONLY" or "no changes"
into the brief on its own, so the agent found the problem and stopped (live
2026-10-02: "have it look at what is wrong with our plugins" became
"Autonomously perform a careful, evidence-based, READ-ONLY audit").
One rule, shared by every surface, so they cannot drift apart.
"""

from __future__ import annotations

from typing import Final

#: Appended to every tool description / instruction that makes Jarvis write a
#: brief for another agent. Kept short: it rides in the voice tool catalog,
#: which has a byte budget (``jarvis.live.tools._CATALOG_BYTE_BUDGET``).
AGENT_BRIEF_RULE: Final[str] = (
    "Write the brief as a work order the agent completes on its own: find the cause, "
    "make the change, verify it, then report what changed. 'Check', 'look into', "
    "'deep dive' or 'audit' include fixing what is found. Never add read-only, "
    "no-changes or report-only limits yourself; use them only when the user "
    "explicitly asked just for analysis, a plan or an answer. Let the agent decide "
    "steps without asking back, except before destructive or outward actions."
)

__all__ = ["AGENT_BRIEF_RULE"]
