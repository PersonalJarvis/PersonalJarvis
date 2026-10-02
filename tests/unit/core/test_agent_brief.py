"""Every surface that briefs another agent carries the same work-order rule.

Without it the model turned "check", "look into" or "deep dive" into a
"READ-ONLY audit" on its own and the agent stopped at a report
(live 2026-10-02). See ``jarvis/core/agent_brief.py``.
"""

from __future__ import annotations

import inspect

from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core.agent_brief import AGENT_BRIEF_RULE
from jarvis.live import native
from jarvis.live.config import LiveConfig
from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool


def test_rule_asks_for_the_work_and_reserves_read_only_for_an_explicit_ask():
    rule = AGENT_BRIEF_RULE.lower()
    assert "make the change" in rule
    assert "never add read-only" in rule
    assert "explicitly asked" in rule
    assert "deep dive" in rule


def test_workspace_prompt_field_carries_the_rule():
    prompt = WorkspaceOrchestrationTool.schema["properties"]["prompt"]
    assert prompt["type"] == "string"
    assert AGENT_BRIEF_RULE in prompt["description"]
    assert "read-only" in WorkspaceOrchestrationTool.description


def test_society_task_field_carries_the_rule():
    task = DelegateToAgentTool.schema["properties"]["task"]["description"]
    assert AGENT_BRIEF_RULE in task


def test_live_delegation_backend_is_instructed_with_the_rule():
    wire = LiveConfig(configured=True, backend_model="chosen-model").session_config(
        language="en", tools=[]
    )
    assert AGENT_BRIEF_RULE in wire["delegation"]["responses"]["instructions"]


def test_native_live_session_instructs_the_rule():
    assert "AGENT_BRIEF_RULE" in inspect.getsource(native.NativeLiveVoiceSession)
