"""Grok Build worker argv + event translation — no live CLI spawn."""
from __future__ import annotations

from pathlib import Path

from jarvis.missions.workers.grok_build_direct_worker import (
    _build_grok_build_cmd,
    _event_looks_like_tool,
    _normalize_model_for_grok_build,
    _text_from_event,
)


def test_cmd_is_headless_subscription_shape(tmp_path: Path) -> None:
    cmd = _build_grok_build_cmd(
        binary="grok",
        prompt="Fix the bug",
        worktree=tmp_path,
        model="grok-4.6",
    )
    assert cmd[:6] == [
        "grok",
        "--no-auto-update",
        "--no-alt-screen",
        "--always-approve",
        "--output-format",
        "streaming-json",
    ]
    assert "--cwd" in cmd
    assert str(tmp_path) in cmd
    assert cmd[-2:] == ["-p", "Fix the bug"]
    assert "-m" in cmd
    assert "grok-4.6" in cmd


def test_cmd_collapses_prompt_newlines(tmp_path: Path) -> None:
    cmd = _build_grok_build_cmd(
        binary="grok",
        prompt="line one\nline two",
        worktree=tmp_path,
    )
    assert cmd[-1] == "line one line two"


def test_foreign_models_are_dropped() -> None:
    assert _normalize_model_for_grok_build("claude-opus-4-8") == ""
    assert _normalize_model_for_grok_build("gpt-5.5") == ""
    assert _normalize_model_for_grok_build("gemini-3.5-flash") == ""
    assert _normalize_model_for_grok_build("grok-4.6") == "grok-4.6"
    assert _normalize_model_for_grok_build("x-ai/grok-4.6") == "grok-4.6"


def test_text_from_plain_and_nested_events() -> None:
    assert _text_from_event({"text": "hello"}) == "hello"
    assert (
        _text_from_event({"update": {"content": {"text": "chunk"}}}) == "chunk"
    )
    assert _text_from_event({"type": "progress"}) is None


def test_tool_event_detection() -> None:
    assert _event_looks_like_tool({"type": "file_write"}) == "Write"
    assert _event_looks_like_tool({"type": "command_execution"}) == "Bash"
    assert _event_looks_like_tool({"type": "assistant"}) is None
