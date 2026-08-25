"""A subscription CLI seat on the Jarvis surface runs AS Jarvis.

The identity (Jarvis' prompt layers + the chat's transcript + the addendum),
where each CLI takes it, the session header the MCP config carries, and the
tool server's approval surface for a request that names a chat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import jarvis_harness, runner_cli
from jarvis.agent_chat.events import make_event
from jarvis.core import runtime_refs
from jarvis.mcp import jarvis_tools_server as server
from jarvis.ui.web import mcp_server_routes


class _Brain:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.asked_with: list[str] = []

    async def render_surface_prompt(self, *, user_text: str) -> tuple[str, str]:
        self.asked_with.append(user_text)
        if self.fail:
            raise RuntimeError("layers unavailable")
        return "YOU ARE GEORGE. Soul, persona, profile.", "Date: today. Awareness: quiet."


@pytest.fixture(autouse=True)
def _clean_refs():
    yield
    runtime_refs._reset_for_tests()


def _history() -> list[dict[str, Any]]:
    return [
        make_event("user_message", {"text": "what did we decide?"}),
        make_event("turn_started", {"turn_id": "t0"}),
        make_event("assistant_text", {"turn_id": "t0", "text": "To ship on Friday."}),
        make_event("turn_finished", {"turn_id": "t0", "status": "done"}),
    ]


# ------------------------------------------------------------ identity text


async def test_identity_has_the_jarvis_layers_the_transcript_and_the_addendum(monkeypatch):
    brain = _Brain()
    monkeypatch.setattr(jarvis_harness, "_brain", lambda: brain)
    text = await jarvis_harness.identity_prompt(
        user_text="and now?", history=_history(), resume=None
    )
    assert text.index("YOU ARE GEORGE") < text.index("Date: today")
    assert "Person: what did we decide?" in text and "Jarvis: To ship on Friday." in text
    assert text.endswith(jarvis_harness.SYSTEM_PREAMBLE)
    assert brain.asked_with == ["and now?"], "the wiki context is pulled for THIS turn's text"


async def test_a_resumed_conversation_carries_no_transcript(monkeypatch):
    monkeypatch.setattr(jarvis_harness, "_brain", lambda: _Brain())
    text = await jarvis_harness.identity_prompt(
        user_text="and now?", history=_history(), resume="vendor-session-1"
    )
    assert "This conversation so far" not in text
    assert "YOU ARE GEORGE" in text


async def test_without_a_brain_the_addendum_stands_alone(monkeypatch):
    monkeypatch.setattr(jarvis_harness, "_brain", lambda: None)
    text = await jarvis_harness.identity_prompt(user_text="hi", history=[], resume=None)
    assert text == jarvis_harness.SYSTEM_PREAMBLE


async def test_failing_layers_never_cost_the_turn(monkeypatch):
    monkeypatch.setattr(jarvis_harness, "_brain", lambda: _Brain(fail=True))
    text = await jarvis_harness.identity_prompt(user_text="hi", history=_history(), resume=None)
    assert text.endswith(jarvis_harness.SYSTEM_PREAMBLE) and "Person:" in text


def test_the_compact_cut_keeps_the_addendum_whole():
    long_text = ("a line of soul\n" * 3000) + "\n\n" + jarvis_harness.SYSTEM_PREAMBLE
    compact = jarvis_harness.compact_identity(long_text, max_chars=4000)
    assert len(compact) <= 4100
    assert compact.endswith(jarvis_harness.SYSTEM_PREAMBLE)
    assert "…" in compact


def test_the_identity_file_lives_in_the_app_data_dir_and_is_removed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(jarvis_harness, "identity_dir", lambda: tmp_path / "identity")
    path = jarvis_harness.write_identity_file("who I am", turn_id="turn-1")
    assert path.parent == tmp_path / "identity" and path.read_text(encoding="utf-8") == "who I am"
    jarvis_harness.remove_identity_file(path)
    assert not path.exists()
    jarvis_harness.remove_identity_file(path)  # twice is fine


# --------------------------------------------------------- what each CLI gets


def _identity(tmp_path: Path) -> jarvis_harness.Identity:
    path = tmp_path / "id.md"
    path.write_text("FULL IDENTITY", encoding="utf-8")
    return jarvis_harness.Identity(
        session_id="sess-1", text="FULL IDENTITY", compact="SHORT IDENTITY", path=path
    )


def test_claude_takes_the_identity_as_a_file_and_names_the_session(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(runner_cli.jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")
    plan = runner_cli.plan_claude(
        prompt="hi",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="ask",
        resume=None,
        identity=_identity(tmp_path),
    )
    argv = plan.argv
    assert "--append-system-prompt-file" in argv
    assert argv[argv.index("--append-system-prompt-file") + 1] == str(tmp_path / "id.md")
    assert "--append-system-prompt" not in argv
    config = argv[argv.index("--mcp-config") + 1]
    assert jarvis_harness.HEADER_NAME in config and "sess-1" in config
    assert plan.stdin_text and "FULL IDENTITY" not in plan.stdin_text


def test_claude_without_identity_spawns_exactly_as_before(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(runner_cli.jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")
    plan = runner_cli.plan_claude(
        prompt="hi", cwd=tmp_path, model="", effort="", permission_mode="ask", resume=None
    )
    assert "--append-system-prompt" in plan.argv
    assert "--append-system-prompt-file" not in plan.argv
    assert jarvis_harness.HEADER_NAME not in plan.argv[plan.argv.index("--mcp-config") + 1]


def test_codex_gets_the_identity_on_stdin_and_the_session_header(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(runner_cli, "read_codex_models", lambda: None)
    monkeypatch.setattr(runner_cli.jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")
    plan = runner_cli.plan_codex(
        prompt="hi",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="ask",
        resume=None,
        identity=_identity(tmp_path),
    )
    assert plan.stdin_text and plan.stdin_text.startswith("<jarvis_identity>\nFULL IDENTITY")
    assert plan.stdin_text.endswith("hi")
    joined = " ".join(plan.argv)
    assert "http_headers" in joined and "sess-1" in joined
    # A resumed conversation already knows who it is.
    resumed = runner_cli.plan_codex(
        prompt="hi",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="ask",
        resume="thread-1",
        identity=_identity(tmp_path),
    )
    assert resumed.stdin_text == "hi"


def test_grok_takes_the_compact_identity_on_argv(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "grok_argv_prefix", lambda: ["grok"])
    plan = runner_cli.plan_grok(
        prompt="hi",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="ask",
        resume=None,
        identity=_identity(tmp_path),
    )
    prompt = plan.argv[plan.argv.index("-p") + 1]
    assert prompt.startswith("<jarvis_identity>\nSHORT IDENTITY") and prompt.endswith("hi")


def test_agy_takes_the_identity_on_stdin(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "agy_argv_prefix", lambda: ["agy"])
    monkeypatch.setattr(runner_cli, "_agy_catalog_cached", lambda: None)
    plan = runner_cli.plan_agy(
        prompt="hi",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="ask",
        resume=None,
        identity=_identity(tmp_path),
    )
    assert plan.stdin_text and plan.stdin_text.startswith("<jarvis_identity>\nFULL IDENTITY")


# ------------------------------------------------------- the session header


def test_the_mcp_route_reads_the_session_header_and_ignores_junk():
    scope = {"headers": [(b"x-jarvis-chat-session", b"0123abcd"), (b"authorization", b"Bearer k")]}
    assert mcp_server_routes.session_ref(scope) == "0123abcd"
    assert mcp_server_routes.session_ref({"headers": []}) is None
    assert mcp_server_routes.session_ref({"headers": [(b"x-jarvis-chat-session", b"../x")]}) is None


def test_the_tool_server_names_the_chats_card_for_a_session_request():
    assert server.approval_snapshot() == {}
    token = server.CHAT_SESSION_REF.set("sess-9")
    try:
        snapshot = server.approval_snapshot()
    finally:
        server.CHAT_SESSION_REF.reset(token)
    assert snapshot["approval_surface"] == "interactive"
    assert snapshot["approval_ref"] == "agent-chat:sess-9"
    assert snapshot["approval_timeout_s"] == server.CHAT_APPROVAL_TIMEOUT_S


def test_the_config_names_the_header_only_with_a_session(monkeypatch):
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")
    plain = jarvis_harness.mcp_config_json() or ""
    named = jarvis_harness.mcp_config_json("sess-2") or ""
    assert jarvis_harness.HEADER_NAME not in plain
    assert jarvis_harness.HEADER_NAME in named and "sess-2" in named
    assert "${JARVIS_CONTROL_API_KEY}" in named, "the key still travels by env, never in the config"
    assert not any("http_headers" in a for a in jarvis_harness.codex_config_args())
    assert any("http_headers" in a for a in jarvis_harness.codex_config_args("sess-2"))
