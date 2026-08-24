"""Permission ladders, the agy wire shape, CLI model catalogs, API effort."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.agent_chat import permissions, runner_cli
from jarvis.agent_chat.catalog import CODEX_FALLBACK_MODELS, provider_row
from jarvis.agent_chat.effort import effort_levels
from jarvis.agent_chat.runner_cli import (
    _AgyState,
    agy_model_args,
    agy_model_catalog,
    plan_agy,
    plan_claude,
    plan_codex,
    translate_agy_line,
)
from jarvis.plugins.brain._anthropic_base import _is_reasoning_model, reasoning_kwargs

# ------------------------------------------------------------ permissions


def test_every_runner_has_a_ladder_with_its_default_on_it():
    for runner in ("claude-cli", "codex-cli", "agy-cli", "grok-cli", "api"):
        ids = permissions.permission_ids(runner)
        assert ids, runner
        assert permissions.default_permission(runner) in ids, runner
        # Plan is the Build | Plan switch; every ladder offers it.
        assert "plan" in ids, runner


@pytest.mark.parametrize(
    ("runner", "picked", "expected"),
    [
        ("claude-cli", "acceptEdits", "acceptEdits"),
        ("claude-cli", "auto", "auto"),  # Claude Code's own classifier mode
        ("claude-cli", "ask", "default"),  # legacy draft value
        ("claude-cli", "full-access", "bypassPermissions"),
        ("codex-cli", "acceptEdits", "auto"),  # Codex's "auto" = workspace-write
        ("codex-cli", "bypassPermissions", "full-access"),
        ("codex-cli", "plan", "plan"),
        ("agy-cli", "default", "accept-edits"),  # ask is never folded onto plan
        ("agy-cli", "bypassPermissions", "skip-permissions"),
        ("api", "acceptEdits", "accept-edits"),
        ("api", "", "ask"),
        ("api", "read-only", "plan"),
    ],
)
def test_normalize_permission_folds_across_runners(runner, picked, expected):
    assert permissions.normalize_permission(runner, picked) == expected


# ------------------------------------------------------------ planners


def test_claude_planner_passes_the_cli_mode_through(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    plan = plan_claude(
        prompt="hi", cwd=tmp_path, model="opus", effort="xhigh", permission_mode="plan", resume=None
    )
    argv = plan.argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert argv[argv.index("--model") + 1] == "opus"
    assert plan.shape == "claude" and plan.keep_stdin
    # The control protocol: NDJSON user message in, prompts answered on stdin.
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
    assert json.loads(plan.stdin_text or "") == {
        "type": "user",
        "message": {"role": "user", "content": "hi"},
    }


def test_claude_planner_opens_the_control_protocol(monkeypatch, tmp_path: Path):
    """The handshake, without which no permission mode can ever ask.

    ``--permission-prompt-tool stdio`` is a flag Claude Code accepts and then
    ignores until a client announces itself over the control protocol. Without
    this frame every tool call ran straight through, so "Ask before acting"
    behaved exactly like "Auto-accept edits" (maintainer report 2026-08-24).
    """
    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    plan = plan_claude(
        prompt="hi", cwd=tmp_path, model="", effort="", permission_mode="default", resume=None
    )
    assert plan.control_init is not None
    frame = json.loads(plan.control_init)
    assert frame["type"] == "control_request"
    assert frame["request"]["subtype"] == "initialize"
    # One frame per line: the CLI reads its stdin as NDJSON.
    assert plan.control_init.endswith("\n")


def test_codex_planner_maps_the_presets(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(runner_cli, "read_codex_models", lambda: None)

    def argv_for(mode: str, effort: str = "high", model: str = "") -> list[str]:
        return plan_codex(
            prompt="p", cwd=tmp_path, model=model, effort=effort, permission_mode=mode, resume=None
        ).argv

    assert 'sandbox_mode="read-only"' in argv_for("read-only")
    assert 'sandbox_mode="workspace-write"' in argv_for("auto")
    assert "--dangerously-bypass-approvals-and-sandbox" in argv_for("full-access")
    approve = argv_for("approve-for-me")
    assert (
        'approvals_reviewer="auto_review"' in approve
        and 'sandbox_mode="workspace-write"' in approve
    )
    plan = plan_codex(
        prompt="p", cwd=tmp_path, model="", effort="high", permission_mode="plan", resume=None
    )
    assert 'sandbox_mode="read-only"' in plan.argv
    assert plan.stdin_text is not None and plan.stdin_text.startswith("PLAN MODE")
    # Never the flags codex exec rejects with exit 2.
    for argv in (argv_for("auto"), argv_for("full-access")):
        assert "--full-auto" not in argv and "-a" not in argv
    assert "model_reasoning_summary=auto" in argv_for("auto")
    # Effort is snapped to the model's own ladder when the catalog knows it.
    monkeypatch.setattr(
        runner_cli,
        "read_codex_models",
        lambda: [
            {"id": "gpt-5.5", "label": "GPT-5.5", "efforts": ["low", "medium", "high", "xhigh"]}
        ],
    )
    assert "model_reasoning_effort=xhigh" in argv_for("auto", effort="ultra", model="gpt-5.5")


def test_codex_resume_uses_the_resume_subcommand(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(runner_cli, "read_codex_models", lambda: None)
    argv = plan_codex(
        prompt="p", cwd=tmp_path, model="", effort="", permission_mode="auto", resume="thread-1"
    ).argv
    assert argv[1:4] == ["exec", "resume", "thread-1"]
    assert "--cd" not in argv  # exec resume has no --cd
    assert argv[-1] == "-"


def test_agy_planner_speaks_agy(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runner_cli, "agy_argv_prefix", lambda: ["agy"])
    monkeypatch.setattr(runner_cli, "_agy_catalog_cached", lambda: None)
    plan = plan_agy(
        prompt="do it",
        cwd=tmp_path,
        model="gemini-3.1-pro",
        effort="medium",
        permission_mode="accept-edits",
        resume=None,
    )
    argv = plan.argv
    assert plan.shape == "agy"
    assert plan.stdin_text == "do it" and "-p" not in argv and "--print" not in argv
    assert argv[argv.index("--mode") + 1] == "accept-edits"
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    assert "--print-timeout" in argv
    # Pro knows only low/high: medium snaps to low, and the pair is sent.
    assert argv[argv.index("--model") + 1] == "gemini-3.1-pro"
    assert argv[argv.index("--effort") + 1] == "low"
    assert plan.env.get("AGY_CLI_HIDE_LOGO") == "1"

    skip = plan_agy(
        prompt="x",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="skip-permissions",
        resume="c1",
    ).argv
    assert (
        "--dangerously-skip-permissions" in skip and skip[skip.index("--conversation") + 1] == "c1"
    )
    planned = plan_agy(
        prompt="x", cwd=tmp_path, model="", effort="", permission_mode="plan", resume=None
    ).argv
    assert planned[planned.index("--mode") + 1] == "plan"


def test_agy_model_args_respects_the_strict_pairing():
    assert agy_model_args("claude-sonnet-4-6", "high") == ["--model", "claude-sonnet-4-6"]
    assert agy_model_args("gpt-oss-120b", "low") == ["--model", "gpt-oss-120b"]
    assert agy_model_args("gemini-3.5-flash", "high") == [
        "--model",
        "gemini-3.5-flash",
        "--effort",
        "high",
    ]
    assert agy_model_args("", "medium") == ["--effort", "medium"]
    # A suffixed id is passed through untouched (no --effort next to it).
    assert agy_model_args("gemini-3.5-flash-low", "high") == ["--model", "gemini-3.5-flash-low"]


def test_agy_model_catalog_folds_suffixed_ids():
    raw = [
        {"id": "gemini-3.7-flash-high", "label": "Gemini 3.7 Flash (High)"},
        {"id": "gemini-3.7-flash-low", "label": "Gemini 3.7 Flash (Low)"},
        {"id": "gemini-3.7-flash-medium", "label": "Gemini 3.7 Flash (Medium)"},
        {"id": "gemini-3.1-pro-high", "label": "Gemini 3.1 Pro (High)"},
        {"id": "gemini-3.1-pro-low", "label": "Gemini 3.1 Pro (Low)"},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (Thinking)"},
        {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)"},
    ]
    rows = agy_model_catalog(raw)
    by_id = {r["id"]: r for r in rows}
    assert by_id["gemini-3.7-flash"] == {
        "id": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "efforts": ["low", "medium", "high"],
    }
    assert by_id["gemini-3.1-pro"]["efforts"] == ["low", "high"]
    assert by_id["claude-sonnet-4-6"]["efforts"] == []
    assert by_id["gpt-oss-120b"]["efforts"] == ["medium"]
    # No list at all -> the fallback table.
    assert agy_model_catalog(None)[0]["id"] == "gemini-3.7-flash"


# ------------------------------------------------------------ agy translation


def _agy_lines() -> list[dict]:
    return [
        {"event": "init", "conversation_id": "conv-1", "init": {"cwd": "C:\\x", "tools": []}},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Let me ",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "look.",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {"name": "run_command", "parameters": {"CommandLine": "echo HELLO"}},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "step_index": 2,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"CommandLine": "echo HELLO"},
                    "output": "HELLO\r\n",
                },
                "duration_seconds": 1.5,
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "step_index": 3,
                "state": "ERROR",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"CommandLine": "rm x"},
                    "error": {"type": "TOOL_ERROR", "message": "user denied permission"},
                },
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "conv-1",
                "status": "SUCCESS",
                "response": "Done: HELLO",
                "duration_seconds": 9.7,
                "num_turns": 1,
                "usage": {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 2},
            },
        },
    ]


def test_translate_agy_line_builds_the_timeline():
    st = _AgyState(turn_id="t1")
    events: list[dict] = []
    for line in _agy_lines():
        events.extend(translate_agy_line(line, st))
    kinds = [e["kind"] for e in events]
    assert kinds == [
        "text_delta",
        "text_delta",
        "assistant_text",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
    ]
    assert events[2]["payload"]["text"] == "Let me look."
    call = events[3]["payload"]
    assert call["name"] == "run_command" and call["summary"] == "echo HELLO"
    result = events[4]["payload"]
    assert result["output"].startswith("HELLO") and result["is_error"] is False
    assert result["duration_ms"] == 1500
    denied = events[6]["payload"]
    assert denied["is_error"] and "denied" in denied["output"]
    assert st.vendor_session == "conv-1"
    assert st.status == "done" and st.result_text == "Done: HELLO"
    assert st.usage == {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 2}


def test_translate_agy_line_reads_failure_from_result_status():
    st = _AgyState(turn_id="t2")
    translate_agy_line(
        {"event": "result", "result": {"status": "ERROR", "error": "timeout waiting for response"}},
        st,
    )
    assert st.status == "error" and "timeout" in (st.error or "")
    st2 = _AgyState(turn_id="t3")
    translate_agy_line({"event": "result", "result": {"status": "CANCELED"}}, st2)
    assert st2.status == "error"


# ------------------------------------------------------------ codex catalog


def test_read_codex_models_reads_the_account_cache(monkeypatch, tmp_path: Path):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-5.5",
                        "display_name": "GPT-5.5",
                        "visibility": "list",
                        "priority": 3,
                        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                    },
                    {
                        "slug": "gpt-5.6-terra",
                        "display_name": "GPT-5.6-Terra",
                        "visibility": "list",
                        "priority": 2,
                        "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "ultra"}],
                        "upgrade": None,
                    },
                    {"slug": "codex-auto-review", "visibility": "hide", "priority": 1},
                    {
                        "slug": "gpt-5.4-mini",
                        "display_name": "GPT-5.4-Mini",
                        "visibility": "list",
                        "priority": 9,
                        "supported_reasoning_levels": [],
                        "upgrade": {
                            "model": "gpt-5.6-luna",
                            "retirement_at": "2026-08-31T19:00:00Z",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner_cli, "_account_env", lambda platform: {"CODEX_HOME": str(home)})
    rows = runner_cli.read_codex_models()
    assert rows is not None
    assert [r["id"] for r in rows] == ["gpt-5.6-terra", "gpt-5.5", "gpt-5.4-mini"]
    assert rows[0]["efforts"] == ["medium", "ultra"]
    assert rows[2]["note"] == "retires 2026-08-31"
    # No cache -> None, and the catalog's fallback carries the picker.
    monkeypatch.setattr(runner_cli, "_account_env", lambda platform: {"CODEX_HOME": str(tmp_path)})
    assert runner_cli.read_codex_models() is None
    assert CODEX_FALLBACK_MODELS[0].id == "gpt-5.6-sol"


def test_the_coding_clis_are_not_offered_as_brains():
    """Codex / Antigravity / Grok Build left the picker with the runner swap.

    They are sub-agents: an agent loop on a subscription seat, with no chat API
    to think with. Their per-model effort ladders still live in effort.py for
    the sub-agent paths that DO drive them.
    """
    for cli in ("antigravity", "openai-codex", "grok-build"):
        assert provider_row(cli) is None, f"{cli} is offered as a brain"
    # The ladders themselves are untouched — the IDE and the missions read them.
    assert "low" in effort_levels("antigravity")


# ------------------------------------------------------------ Anthropic API effort


def test_anthropic_adapter_effort_and_temperature_rules():
    # Claude 5 / 4.x families: no temperature.
    for model in ("claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"):
        assert _is_reasoning_model(model), model
    assert not _is_reasoning_model("claude-3-5-sonnet-20241022")
    # No effort requested -> nothing added (the voice brain's requests stay as they were).
    assert reasoning_kwargs("claude-opus-5", None) == {}
    assert reasoning_kwargs("claude-opus-5", "") == {}
    # Effort -> output_config.effort + adaptive thinking on the models that take it.
    assert reasoning_kwargs("claude-opus-5", "xhigh") == {
        "output_config": {"effort": "xhigh"},
        "thinking": {"type": "adaptive"},
    }
    # Opus 4.6 has no xhigh: snaps to high; Opus 4.5 tops out at high.
    assert reasoning_kwargs("claude-opus-4-6", "xhigh")["output_config"] == {"effort": "high"}
    assert reasoning_kwargs("claude-opus-4-5", "max")["output_config"] == {"effort": "high"}
    assert "thinking" not in reasoning_kwargs("claude-opus-4-5", "max")
    # Haiku 4.5 rejects the parameter: nothing is sent.
    assert reasoning_kwargs("claude-haiku-4-5-20251001", "high") == {}
    # none/minimal mean "as little as possible" -> the lowest rung.
    assert reasoning_kwargs("claude-sonnet-5", "none")["output_config"] == {"effort": "low"}
