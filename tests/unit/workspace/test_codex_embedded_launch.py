"""Keep Codex's oversized startup update box out of embedded terminal panes."""

import pytest

from jarvis.agentic_ide import session
from jarvis.agentic_ide.agent_sessions import ResumeHandle, resume_argv
from jarvis.workspace.agents import get_agent


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("continuing", [False, True])
def test_embedded_codex_suppresses_update_box_on_every_launch(
    monkeypatch: pytest.MonkeyPatch, remote: bool, continuing: bool
) -> None:
    # An executable path takes the same argv path on POSIX and Windows;
    # test_session covers the Windows npm shim separately.
    monkeypatch.setattr(session.shutil, "which", lambda _name: "/tools/codex")
    launcher = session.remote_agent_argv if remote else session.agent_argv
    argv = launcher("codex")
    assert argv is not None
    if continuing:
        resume = resume_argv(
            "codex", ResumeHandle(kind="codex_rollout", id="conversation", captured_at=1.0)
        )
        assert resume is not None
        argv += resume
        assert argv[-2:] == ("resume", "conversation")

    assert argv[1:3] == ("-c", "check_for_update_on_startup=false")
    # Only pane launches get the override, never detection or the plain CLI
    # command used outside the embedded IDE.
    agent = get_agent("codex")
    assert agent is not None and agent.spec is not None
    assert agent.launch_command == "codex"
    assert agent.spec.check_command == ("codex", "--version")
