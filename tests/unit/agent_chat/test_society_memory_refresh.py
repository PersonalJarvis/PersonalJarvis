"""Fresh agent knowledge reaches a resumed vendor conversation."""

from jarvis.agent_chat.jarvis_harness import COMPACT_MAX_CHARS, Identity, society_memory_refresh
from jarvis.agent_chat.runner_cli import _with_identity


def test_resumed_cli_gets_current_rules_memory_and_skills_without_old_transcript():
    text = (
        "## You are Scout\nResearcher\n\n"
        "## Standing instructions\nDraft only.\n\n"
        "## Your memory\nCurrent durable fact.\n\n"
        "## Learned working instructions\nCheck source dates.\n\n"
        "## Your learned skills (run one with society_run_skill)\n- verification\n\n"
        "## This conversation so far\nOLD TRANSCRIPT\n"
    )
    identity = Identity(session_id="society:scout", text=text, compact=text)
    prompt = _with_identity("Next task", identity, "vendor-session")
    for value in ["Draft only.", "Current durable fact.", "Check source dates.", "verification"]:
        assert value in prompt
    assert "OLD TRANSCRIPT" not in prompt
    assert prompt.endswith("Next task")


def test_normal_cli_sessions_keep_their_resume_behavior():
    identity = Identity(session_id="normal-chat", text="Original identity", compact="Original")
    assert _with_identity("Next task", identity, "vendor-session") == "Next task"


def test_compact_transport_retains_argv_budget_and_reports_omissions():
    output = society_memory_refresh("## Your memory\n" + "Long entry.\n" * 3000, compact=True)
    assert len(output) < COMPACT_MAX_CHARS
    assert "Omitted entries are not deletions" in output


def test_cleared_memory_explicitly_replaces_previous_snapshot():
    output = society_memory_refresh("## You are Scout\n## Your memory\nNothing remembered yet.")
    assert "Nothing remembered yet." in output
    assert "An absent section has no current entries" in output
