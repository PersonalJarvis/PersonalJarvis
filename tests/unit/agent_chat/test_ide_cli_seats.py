"""The Agentic IDE's coding CLIs are the chat's coding CLIs — every one, as a chat.

Binds the chat catalog (``jarvis.agent_chat.catalog``) to the IDE's registry
(``jarvis.workspace.agents``) and to the planners that drive each CLI without
a terminal (``jarvis.agent_chat.runner_cli``), and pins the wire shapes of the
CLIs that joined on 2026-08-27 — OpenCode, Kimi Code, GLM Coding Plan, DeepSeek
Harness — against lines captured from the installed binaries.

The drift this guards against is the quiet kind: a CLI the IDE offers as a
pane and the chat does not, or a chat seat whose turn falls back to "No runner
for provider" — both are a picker that lies.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import catalog, effort, permissions, typeahead
from jarvis.agent_chat import runner_cli as rc
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.agent_chat.store import AgentChatSession
from jarvis.workspace import agents as workspace_agents

# ------------------------------------------------------------------ parity


def _ide_coding_clis() -> list[str]:
    """The registry's built-in coding CLIs, in the registry's own order."""
    return [
        name
        for name in workspace_agents.builtin_names()
        if (entry := workspace_agents.get_agent(name)) is not None and entry.is_coding_agent
    ]


def test_every_ide_coding_cli_has_a_chat_seat_with_a_planner() -> None:
    """Registry vs. catalog vs. planners.

    A CLI the IDE opens as a pane must be a row in the chat's picker, and that
    row's runner must be one the CLI runner can actually drive — otherwise the
    seat exists and every turn on it ends in "No runner for provider".
    """
    rows = {row.agent: row for row in catalog.cli_rows()}
    for name in _ide_coding_clis():
        assert name in rows, f"{name} is a pane in the IDE but not a seat in the chat"
        assert rc.supports_cli_runner(rows[name].runner), name
        assert rows[name].runner in rc.CLI_BINARIES, name


def test_every_chat_cli_seat_names_a_registered_ide_entry_in_the_ide_order() -> None:
    for row in catalog.cli_rows():
        assert workspace_agents.get_agent(row.agent) is not None, row.id
    # The same order in both pickers, so a person finds one CLI in one place.
    assert [row.agent for row in catalog.cli_rows()] == _ide_coding_clis()


def test_the_agent_surface_offers_every_cli_seat_and_the_front_page_none() -> None:
    agent_ids = {row.id for row in catalog.rows_for("agent")}
    jarvis_ids = {row.id for row in catalog.rows_for("jarvis")}
    for row in catalog.cli_rows():
        assert row.id in agent_ids, row.id
        # The dual Claude row is the Anthropic API on the front page; every
        # other CLI seat has no API twin there and stays out.
        if row.id != "claude-api":
            assert row.id not in jarvis_ids, row.id


def test_a_cli_the_ide_dropped_leaves_the_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "_ide_has", lambda agent: agent != "kimi")
    ids = [row.id for row in catalog.rows_for("agent")]
    assert "kimi" not in ids
    assert "opencode" in ids and "openai" in ids


def test_resolve_runner_names_each_clis_own_runner() -> None:
    from jarvis.agent_chat.service import resolve_runner

    assert resolve_runner("opencode") == "opencode-cli"
    assert resolve_runner("kimi") == "kimi-cli"
    assert resolve_runner("glm") == "glm-cli"
    assert resolve_runner("deepseek-harness") == "dsh-cli"
    assert resolve_runner("cursor") == "cursor-cli"
    for pid in ("opencode", "kimi", "glm", "deepseek-harness", "cursor"):
        assert resolve_runner(pid, surface="jarvis") == "unknown"


def test_the_row_dict_carries_the_registry_key() -> None:
    opencode = catalog.provider_row("opencode")
    openai = catalog.provider_row("openai")
    assert opencode is not None and opencode.to_dict()["agent"] == "opencode"
    assert openai is not None and openai.to_dict()["agent"] == ""


# ----------------------------------------------------------------- ladders


def test_every_cli_runner_has_a_permission_ladder_and_a_typeahead_answer() -> None:
    for runner in rc.CLI_RUNNERS:
        assert permissions.permission_modes(runner), runner
        assert permissions.default_permission(runner) in permissions.permission_ids(runner)
        assert isinstance(typeahead.triggers_for(runner), tuple)
    # GLM Coding Plan IS Claude Code: same words, same commands after "/".
    assert permissions.permission_modes("glm-cli") == permissions.permission_modes("claude-cli")
    assert typeahead.triggers_for("glm-cli") == typeahead.triggers_for("claude-cli")
    assert typeahead.triggers_for("dsh-cli") == ()


@pytest.mark.parametrize(
    ("runner", "picked", "expected"),
    [
        ("opencode-cli", "bypassPermissions", "auto"),
        ("opencode-cli", "plan", "plan"),
        ("opencode-cli", "acceptEdits", "default"),
        ("kimi-cli", "ask", "auto"),
        ("kimi-cli", "plan", "plan"),
        ("dsh-cli", "plan", "auto"),
        ("glm-cli", "bypass", "bypassPermissions"),
        ("cursor-cli", "plan", "plan"),
        ("cursor-cli", "bypassPermissions", "auto"),
        ("cursor-cli", "acceptEdits", "ask"),
    ],
)
def test_a_mode_from_another_ladder_folds_onto_the_new_clis(
    runner: str, picked: str, expected: str
) -> None:
    assert permissions.normalize_permission(runner, picked) == expected


def test_the_new_clis_offer_no_effort_knob() -> None:
    for provider in ("opencode", "kimi", "glm", "deepseek-harness", "cursor"):
        assert effort.effort_levels(provider) == ("",)
        assert effort.default_effort(provider) == ""
        assert effort.normalize_effort(provider, "high") == ""


# ------------------------------------------------------------ opencode wire

# Captured from ``opencode run --format json`` (opencode 1.18.23), trimmed.
_OC_TEXT = {
    "type": "text",
    "timestamp": 1787847908825,
    "sessionID": "ses_1",
    "part": {
        "id": "prt_t",
        "messageID": "msg_1",
        "sessionID": "ses_1",
        "type": "text",
        "text": "pong",
        "time": {"start": 1787847908734, "end": 1787847908810},
    },
}
_OC_REASONING = {
    "type": "reasoning",
    "timestamp": 1787849182569,
    "sessionID": "ses_1",
    "part": {
        "id": "prt_r",
        "messageID": "msg_1",
        "type": "reasoning",
        "text": "The user wants exactly one word.",
        "time": {"start": 1787849182500, "end": 1787849182560},
    },
}
_OC_TOOL = {
    "type": "tool_use",
    "timestamp": 1787848055718,
    "sessionID": "ses_1",
    "part": {
        "type": "tool",
        "tool": "read",
        "callID": "call_1",
        "state": {
            "status": "completed",
            "input": {"filePath": "C:\\w\\note.txt"},
            "output": "<content>\n1: hello\n</content>",
            "title": "w\\note.txt",
            "time": {"start": 1787848055697, "end": 1787848055715},
        },
        "id": "prt_x",
        "sessionID": "ses_1",
        "messageID": "msg_1",
    },
}
_OC_FINISH = {
    "type": "step_finish",
    "timestamp": 1787847908825,
    "sessionID": "ses_1",
    "part": {
        "id": "prt_f",
        "reason": "stop",
        "messageID": "msg_1",
        "type": "step-finish",
        "tokens": {
            "total": 21445,
            "input": 19649,
            "output": 4,
            "reasoning": 0,
            "cache": {"write": 0, "read": 1792},
        },
        "cost": 0.0125,
    },
}
_OC_ERROR = {
    "type": "error",
    "timestamp": 1787848313951,
    "sessionID": "ses_1",
    "error": {
        "name": "APIError",
        "data": {"message": "Upstream request failed: Endpoint is unavailable.", "statusCode": 503},
    },
}


def _kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


def test_opencode_lines_become_text_reasoning_tool_rows_and_usage() -> None:
    st = rc._OpenCodeState(turn_id="t")
    events = rc.translate_opencode_line(_OC_REASONING, st)
    assert _kinds(events) == ["reasoning"]
    assert events[0]["payload"]["text"].startswith("The user wants")
    assert events[0]["payload"]["duration_ms"] == 60

    events = rc.translate_opencode_line(_OC_TOOL, st)
    assert _kinds(events) == ["tool_call", "tool_result"]
    call, result = events[0]["payload"], events[1]["payload"]
    assert call["name"] == "read" and call["call_id"] == "call_1"
    assert call["summary"] == "C:\\w\\note.txt"
    assert result["call_id"] == "call_1" and not result["is_error"]
    assert "hello" in result["output"] and result["duration_ms"] == 18
    # The same part reported again (a later status) is one row, not two.
    assert _kinds(rc.translate_opencode_line(_OC_TOOL, st)) == ["tool_result"]

    events = rc.translate_opencode_line(_OC_TEXT, st)
    assert _kinds(events) == ["assistant_text"]
    assert events[0]["payload"]["text"] == "pong"
    assert st.emitted_text and st.vendor_session == "ses_1"

    assert rc.translate_opencode_line(_OC_FINISH, st) == []
    assert st.usage == {
        "input_tokens": 19649,
        "output_tokens": 4,
        "reasoning_output_tokens": 0,
        "cached_input_tokens": 1792,
        "cache_write_input_tokens": 0,
    }
    assert st.cost_usd == pytest.approx(0.0125)
    assert st.status == "done"


def test_an_opencode_error_ends_the_turn_unless_the_retry_answered() -> None:
    st = rc._OpenCodeState(turn_id="t")
    assert rc.translate_opencode_line(_OC_ERROR, st) == []
    assert st.status == "error" and "Endpoint is unavailable" in (st.error or "")
    # A retry that got through: the answer wins over the error before it.
    rc.translate_opencode_line(_OC_TEXT, st)
    assert st.status == "done" and st.error is None


# ---------------------------------------------------------------- kimi wire

# The shape ``PromptJsonWriter`` writes (kimi-code 0.29.2).
_KIMI_STEP = {
    "role": "assistant",
    "content": "Let me read it.",
    "tool_calls": [
        {
            "type": "function",
            "id": "call_a",
            "function": {"name": "ReadFile", "arguments": '{"path": "note.txt"}'},
        }
    ],
}
_KIMI_RESULT = {"role": "tool", "tool_call_id": "call_a", "content": "hello"}
_KIMI_META = {"role": "meta", "type": "turn.step.retrying", "failed_attempt": 1}
_KIMI_FINAL = {"role": "assistant", "content": "It says hello."}


def test_kimi_lines_become_messages_and_tool_rows() -> None:
    st = rc._KimiState(turn_id="t")
    events = rc.translate_kimi_line(_KIMI_STEP, st)
    assert _kinds(events) == ["assistant_text", "tool_call"]
    assert events[0]["payload"]["text"] == "Let me read it."
    call = events[1]["payload"]
    assert call["name"] == "ReadFile" and call["call_id"] == "call_a"
    # Arguments arrive as a JSON string and are handed on as the dict they are.
    assert call["input"] == {"path": "note.txt"} and call["summary"] == "note.txt"

    events = rc.translate_kimi_line(_KIMI_RESULT, st)
    assert _kinds(events) == ["tool_result"]
    assert events[0]["payload"]["call_id"] == "call_a"
    assert events[0]["payload"]["output"] == "hello"

    assert rc.translate_kimi_line(_KIMI_META, st) == []
    events = rc.translate_kimi_line(_KIMI_FINAL, st)
    assert _kinds(events) == ["assistant_text"]
    # Each assistant line is its own message on the timeline.
    assert events[0]["payload"]["message_id"] != "kimi-1"
    assert st.result_text == "It says hello." and st.emitted_text


def test_kimi_arguments_that_are_not_json_are_kept_rather_than_dropped() -> None:
    assert rc._kimi_arguments("not json") == {"arguments": "not json"}
    assert rc._kimi_arguments('["a"]') == {"arguments": ["a"]}
    assert rc._kimi_arguments({"x": 1}) == {"x": 1}
    assert rc._kimi_arguments(None) == {}


# ---------------------------------------------------------------- planners


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "opencode_argv_prefix", lambda: ["opencode"])
    monkeypatch.setattr(rc, "kimi_argv_prefix", lambda: ["kimi"])
    monkeypatch.setattr(rc, "dsh_argv_prefix", lambda: ["dsh"])
    monkeypatch.setattr(rc, "claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(rc, "_account_env", lambda platform: {"HOME": "h"})
    monkeypatch.setattr(rc, "_registry_env", lambda agent, base: {**base, "AGENT": agent})
    monkeypatch.setattr(rc.jarvis_harness, "mcp_config_json", lambda sid=None: None)


def test_plan_opencode_speaks_run_json_and_the_stance_flags(stubbed: None) -> None:
    plan = rc.plan_opencode(
        prompt="-- fix it",
        cwd=Path("."),
        model="anthropic/claude-opus-5",
        effort="",
        permission_mode="auto",
        resume=None,
    )
    assert plan.argv[:5] == ["opencode", "run", "--format", "json", "--thinking"]
    assert "--auto" in plan.argv
    # A sentence that starts with a dash is a sentence: it rides behind ``--``.
    assert plan.argv[-2:] == ["--", "-- fix it"]
    assert plan.argv[plan.argv.index("--model") + 1] == "anthropic/claude-opus-5"
    assert plan.shape == "opencode" and plan.env["AGENT"] == "opencode"
    assert plan.stdin_text is None and plan.vendor_session is None

    plan = rc.plan_opencode(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="plan", resume="ses_9"
    )
    assert plan.argv[plan.argv.index("--agent") + 1] == "plan"
    assert plan.argv[plan.argv.index("--session") + 1] == "ses_9"
    assert "--auto" not in plan.argv and plan.vendor_session == "ses_9"

    plan = rc.plan_opencode(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="default", resume=None
    )
    assert "--auto" not in plan.argv and "--agent" not in plan.argv


def test_plan_kimi_prints_json_and_finds_its_session_afterwards(stubbed: None) -> None:
    plan = rc.plan_kimi(
        prompt="hi", cwd=Path("."), model="k2", effort="", permission_mode="auto", resume=None
    )
    assert plan.argv[:3] == ["kimi", "--output-format", "stream-json"]
    assert plan.argv[plan.argv.index("--model") + 1] == "k2"
    assert "--auto" in plan.argv
    assert plan.argv[-2:] == ["--prompt", "hi"]
    assert plan.shape == "kimi" and plan.env["AGENT"] == "kimi"
    # Fresh conversation: the id is looked up in Kimi's store afterwards.
    assert plan.discover is rc._kimi_session_after

    plan = rc.plan_kimi(
        prompt="hi", cwd=Path("."), model="", effort="", permission_mode="plan", resume="session_1"
    )
    assert plan.argv[plan.argv.index("--session") + 1] == "session_1"
    assert plan.argv[-1].startswith("PLAN MODE:") and plan.argv[-1].endswith("hi")
    assert plan.discover is None and plan.vendor_session == "session_1"


def test_plan_cursor_prints_stream_json_and_maps_the_stances(
    stubbed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rc, "cursor_argv_prefix", lambda: ["agent"])
    plan = rc.plan_cursor(
        prompt="fix it",
        cwd=Path("."),
        model="composer-2",
        effort="high",
        permission_mode="auto",
        resume=None,
    )
    assert plan.argv[:4] == ["agent", "-p", "--output-format", "stream-json"]
    assert "--force" in plan.argv
    assert "--mode" not in plan.argv
    assert plan.argv[plan.argv.index("--model") + 1] == "composer-2"
    assert plan.argv[-1] == "fix it"
    assert plan.shape == "cursor" and plan.env["AGENT"] == "cursor"
    assert plan.vendor_session is None

    plan = rc.plan_cursor(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="plan", resume="chat-9"
    )
    assert plan.argv[plan.argv.index("--mode") + 1] == "plan"
    assert "--force" not in plan.argv
    assert plan.argv[plan.argv.index("--resume") + 1] == "chat-9"
    assert plan.vendor_session == "chat-9"

    plan = rc.plan_cursor(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="ask", resume=None
    )
    assert plan.argv[plan.argv.index("--mode") + 1] == "ask"
    assert "--force" not in plan.argv


def test_plan_dsh_is_one_task_in_plain_text(stubbed: None) -> None:
    plan = rc.plan_dsh(
        prompt="run the tests", cwd=Path("."), model="", effort="", permission_mode="", resume=None
    )
    assert plan.argv == ["dsh", "--profile", "headless", "run the tests"]
    assert plan.shape == "text" and plan.vendor_session is None
    assert plan.env["AGENT"] == "deepseek-harness"


def test_plan_glm_is_claude_codes_plan_on_zais_environment(stubbed: None) -> None:
    plan = rc.plan_glm(
        prompt="hi",
        cwd=Path("."),
        model="opus",
        effort="high",
        permission_mode="acceptEdits",
        resume=None,
    )
    assert plan.argv[0] == "claude" and plan.shape == "claude"
    assert plan.argv[plan.argv.index("--permission-mode") + 1] == "acceptEdits"
    assert plan.argv[plan.argv.index("--model") + 1] == "opus"
    # The effort flag belongs to Anthropic's endpoint, not Z.ai's.
    assert "--effort" not in plan.argv
    assert plan.keep_stdin and plan.control_init is not None
    assert plan.env["AGENT"] == "glm"


def test_registry_env_applies_the_panes_overlay_and_refuses_an_unconfigured_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = SimpleNamespace(
        display_name="GLM Coding Plan",
        spawn_env=(("KEEP", "1"),),
        spawn_env_factory=lambda: {"ANTHROPIC_BASE_URL": "https://z", "ANTHROPIC_API_KEY": ""},
    )
    monkeypatch.setattr(workspace_agents, "get_agent", lambda name: configured)
    env = rc._registry_env("glm", {"ANTHROPIC_API_KEY": "host-key", "PATH": "p"})
    assert env["KEEP"] == "1" and env["ANTHROPIC_BASE_URL"] == "https://z"
    # An empty value REMOVES the variable — the host's key must not outrank
    # the token the endpoint is given.
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"] == "p"

    unconfigured = SimpleNamespace(
        display_name="GLM Coding Plan", spawn_env=(), spawn_env_factory=lambda: None
    )
    monkeypatch.setattr(workspace_agents, "get_agent", lambda name: unconfigured)
    with pytest.raises(rc.CliUnavailable, match="GLM Coding Plan is not configured"):
        rc._registry_env("glm", {})

    monkeypatch.setattr(workspace_agents, "get_agent", lambda name: None)
    assert rc._registry_env("nobody", {"A": "b"})["A"] == "b"


def test_opencode_models_reads_provider_slash_model_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    out = (
        b"opencode/big-pickle\n"
        b"anthropic/claude-opus-5\n"
        b"cloudflare-ai-gateway/anthropic/claude-opus-5\n"
        b"\n"
        b"not a model\n"
    )
    monkeypatch.setattr(rc, "opencode_argv_prefix", lambda: ["opencode"])
    monkeypatch.setattr(rc, "_registry_env", lambda agent, base: base)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out, returncode=0)
    )
    rc._OPENCODE_CATALOG["rows"] = None
    rows = rc.read_opencode_models()
    assert rows == [
        {"id": "opencode/big-pickle", "label": "big-pickle", "note": "opencode"},
        {"id": "anthropic/claude-opus-5", "label": "claude-opus-5", "note": "anthropic"},
        {
            "id": "cloudflare-ai-gateway/anthropic/claude-opus-5",
            "label": "anthropic/claude-opus-5",
            "note": "cloudflare-ai-gateway",
        },
    ]
    rc._OPENCODE_CATALOG["rows"] = None


# ------------------------------------------------------------------ the pump


def _fake_cli(lines: list[dict | str], exit_code: int = 0) -> list[str]:
    """argv for a process that prints ``lines`` (dicts as JSON) and exits."""
    body = "import sys\n"
    for line in lines:
        text = json.dumps(line) if isinstance(line, dict) else line
        body += f"sys.stdout.write({json.dumps(text)} + chr(10))\n"
    body += f"sys.exit({exit_code})\n"
    return [sys.executable, "-c", body]


def _turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: str,
    plan: rc.CliPlan,
    provider: str,
) -> tuple[str | None, list[dict]]:
    monkeypatch.setitem(rc._PLANNERS, runner, lambda **kw: plan)
    events: list[dict] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    async def ask(*_: object) -> str:
        return "deny"

    session = AgentChatSession(
        session_id="s1",
        title="",
        provider=provider,
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode="",
        vendor_session=None,
        created_ms=0,
        updated_ms=0,
        message_count=0,
        preview="",
    )
    handle = TurnHandle(
        session=session, turn_id="t1", emit=emit, request_approval=ask, cancel=asyncio.Event()
    )
    vendor = asyncio.run(rc.run_cli_turn(handle, "hi", runner))
    return vendor, events


def test_an_opencode_turn_runs_through_the_shared_pump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = rc.CliPlan(
        _fake_cli([_OC_REASONING, _OC_TOOL, _OC_TEXT, _OC_FINISH]),
        dict(os.environ),
        None,
        "opencode",
        None,
    )
    vendor, events = _turn(monkeypatch, tmp_path, "opencode-cli", plan, "opencode")
    assert vendor == "ses_1"
    assert _kinds(events) == [
        "reasoning",
        "tool_call",
        "tool_result",
        "assistant_text",
        "turn_finished",
    ]
    finished = events[-1]["payload"]
    assert finished["status"] == "done" and finished["error"] is None
    assert finished["usage"]["input_tokens"] == 19649
    assert finished["cost_usd"] == pytest.approx(0.0125)


def test_a_plain_text_cli_is_shown_as_it_prints_and_kept_as_the_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = rc.CliPlan(
        _fake_cli(["pong", "and a second line"]), dict(os.environ), None, "text", None
    )
    vendor, events = _turn(monkeypatch, tmp_path, "dsh-cli", plan, "deepseek-harness")
    assert vendor is None
    assert _kinds(events) == ["text_delta", "text_delta", "assistant_text", "turn_finished"]
    assert events[2]["payload"]["text"] == "pong\nand a second line"
    assert events[2]["payload"]["message_id"] == events[0]["payload"]["message_id"]
    assert events[-1]["payload"]["status"] == "done"


def test_a_kimi_turn_discovers_its_session_once_the_cli_has_exited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[Path, float]] = []

    def discover(cwd: Path, started_at: float) -> str | None:
        seen.append((cwd, started_at))
        return "session_abc"

    plan = rc.CliPlan(
        _fake_cli([_KIMI_STEP, _KIMI_RESULT, _KIMI_FINAL]),
        dict(os.environ),
        None,
        "kimi",
        None,
        discover=discover,
    )
    vendor, events = _turn(monkeypatch, tmp_path, "kimi-cli", plan, "kimi")
    assert vendor == "session_abc"
    assert seen and seen[0][0] == tmp_path
    assert _kinds(events) == [
        "assistant_text",
        "tool_call",
        "tool_result",
        "assistant_text",
        "turn_finished",
    ]
    assert events[-1]["payload"]["status"] == "done"


# Cursor print-mode stream-json, from the documented example sequence.
_CURSOR_ASSISTANT = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "I'll read the README.md file"}],
    },
    "session_id": "c6b62c6f-7ead-4fd6-9922-e952131177ff",
}
_CURSOR_TOOL_START = {
    "type": "tool_call",
    "subtype": "started",
    "call_id": "toolu_1",
    "tool_call": {"readToolCall": {"args": {"path": "README.md"}}},
    "session_id": "c6b62c6f-7ead-4fd6-9922-e952131177ff",
}
_CURSOR_TOOL_DONE = {
    "type": "tool_call",
    "subtype": "completed",
    "call_id": "toolu_1",
    "tool_call": {
        "readToolCall": {
            "args": {"path": "README.md"},
            "result": {"success": {"content": "# Project\n", "totalLines": 54}},
        }
    },
    "session_id": "c6b62c6f-7ead-4fd6-9922-e952131177ff",
}
_CURSOR_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 1234,
    "result": "I'll read the README.md fileDone!",
    "session_id": "c6b62c6f-7ead-4fd6-9922-e952131177ff",
}


def test_cursor_lines_become_text_and_tool_rows() -> None:
    st = rc._CursorState(turn_id="t")
    events = rc.translate_cursor_line(_CURSOR_ASSISTANT, st)
    assert _kinds(events) == ["assistant_text"]
    assert events[0]["payload"]["text"] == "I'll read the README.md file"
    assert st.vendor_session == "c6b62c6f-7ead-4fd6-9922-e952131177ff"

    events = rc.translate_cursor_line(_CURSOR_TOOL_START, st)
    assert _kinds(events) == ["tool_call"]
    assert events[0]["payload"]["name"] == "read"
    assert events[0]["payload"]["summary"] == "README.md"

    events = rc.translate_cursor_line(_CURSOR_TOOL_DONE, st)
    assert _kinds(events) == ["tool_result"]
    assert "# Project" in events[0]["payload"]["output"]
    assert not events[0]["payload"]["is_error"]

    # The result's concatenated text is not a second assistant row — the
    # earlier message already is the answer.
    assert rc.translate_cursor_line(_CURSOR_RESULT, st) == []
    assert st.status == "done" and st.emitted_text


def test_a_cursor_turn_runs_through_the_shared_pump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = rc.CliPlan(
        _fake_cli([_CURSOR_ASSISTANT, _CURSOR_TOOL_START, _CURSOR_TOOL_DONE, _CURSOR_RESULT]),
        dict(os.environ),
        None,
        "cursor",
        None,
    )
    vendor, events = _turn(monkeypatch, tmp_path, "cursor-cli", plan, "cursor")
    assert vendor == "c6b62c6f-7ead-4fd6-9922-e952131177ff"
    assert _kinds(events) == [
        "assistant_text",
        "tool_call",
        "tool_result",
        "turn_finished",
    ]
    assert events[-1]["payload"]["status"] == "done"


def test_a_cli_that_dies_silently_reports_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = rc.CliPlan(_fake_cli([], exit_code=3), dict(os.environ), None, "opencode", None)
    vendor, events = _turn(monkeypatch, tmp_path, "opencode-cli", plan, "opencode")
    assert vendor is None
    assert _kinds(events) == ["turn_finished"]
    finished = events[-1]["payload"]
    assert finished["status"] == "error" and "exited with code 3" in finished["error"]
