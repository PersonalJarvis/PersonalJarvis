"""Tests for the capability-honesty gate's tool-call evidence extraction.

Live mission 019eb17d-710a (2026-06-10): a codex worker REALLY analysed the
user's Gmail inbox (sub-agent thread), REALLY wrote email-analyse.html
(35 KB, real subjects verified on disk) — and the honesty gate still
overrode the verdict to "Worker claimed success but made no tool call",
because `_extract_tool_call_evidence` only recognised the Claude
stream-json `"type":"tool_use"` shape. The codex `--json` stream encodes
real actions as `item.started`/`item.completed` events with item types
`command_execution` / `file_change` / `mcp_tool_call` instead. Each
12-minute iteration was discarded until the critic loops were exhausted.

The gate's purpose stays intact: prose-only output ("I have sent the
email") must still yield ZERO evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions.critic.runner import (
    CriticRunner,
    _extract_tool_call_evidence,
    enforce_capability_honesty,
)
from jarvis.missions.critic.verdict import (
    REQUIRED_AXES,
    CriticAxis,
    CriticVerdict,
)
from tests.missions.critic.test_runner_claude_direct import (
    _patch_direct,
    _valid_verdict_json,
)


def _codex_item_line(item: dict) -> str:
    return json.dumps({"type": "item.completed", "item": item})


def _approval_verdict() -> CriticVerdict:
    return CriticVerdict(
        verdict="approve",
        axes={ax: CriticAxis(status="pass", evidence=["ok"]) for ax in REQUIRED_AXES},
        summary="looks good",
        summary_de="sieht gut aus",  # i18n-allow: German TTS variant fixture
        confidence=0.9,
        suggested_next_action="accept",
    )


# --- codex stream: real actions must count as evidence ---


def test_codex_command_execution_counts_as_evidence() -> None:
    stream = _codex_item_line(
        {
            "id": "item_1",
            "type": "command_execution",
            "command": "powershell.exe -Command 'Get-Content email-analyse.html'",
            "aggregated_output": "<!DOCTYPE html>...",
            "status": "completed",
        }
    )
    assert _extract_tool_call_evidence(stream)


def test_codex_file_change_counts_as_evidence() -> None:
    stream = _codex_item_line(
        {
            "id": "item_57",
            "type": "file_change",
            "changes": [{"path": "email-analyse.html", "kind": "add"}],
            "status": "completed",
        }
    )
    assert _extract_tool_call_evidence(stream)


def test_codex_mcp_tool_call_counts_as_evidence() -> None:
    stream = _codex_item_line(
        {
            "id": "item_9",
            "type": "mcp_tool_call",
            "server": "gmail",
            "tool": "search_messages",
            "status": "completed",
        }
    )
    assert _extract_tool_call_evidence(stream)


def test_codex_prose_only_is_not_evidence() -> None:
    """agent_message prose alone must keep yielding zero evidence."""
    stream = "\n".join(
        [
            _codex_item_line(
                {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "I have sent the email and created the file.",
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"output_tokens": 10}}),
        ]
    )
    assert _extract_tool_call_evidence(stream) == ()


def test_codex_escaped_format_string_in_prose_is_not_evidence() -> None:
    """A fabricating worker quoting the stream format must not mint evidence.

    In the raw NDJSON line the quoted prose appears JSON-escaped
    (\\"type\\":\\"command_execution\\"); only a REAL item field (unescaped
    quotes) may count.
    """
    stream = _codex_item_line(
        {
            "id": "item_0",
            "type": "agent_message",
            "text": 'I ran a "type":"command_execution" action, trust me.',
        }
    )
    assert _extract_tool_call_evidence(stream) == ()


def test_codex_item_started_counts_as_evidence() -> None:
    stream = json.dumps(
        {
            "type": "item.started",
            "item": {
                "id": "item_2",
                "type": "command_execution",
                "command": "git status",
                "aggregated_output": "",
                "status": "in_progress",
            },
        }
    )
    assert _extract_tool_call_evidence(stream)


def test_codex_web_search_counts_as_evidence() -> None:
    stream = _codex_item_line(
        {"id": "item_4", "type": "web_search", "query": "python sqlite vacuum"}
    )
    assert _extract_tool_call_evidence(stream)


def test_claude_tool_use_still_recognized() -> None:
    stream = '{"type": "tool_use", "id": "tu_1", "name": "Write", "input": {}}'
    assert "Write" in _extract_tool_call_evidence(stream)


# --- full gate behaviour ---


def test_gate_passes_codex_worker_with_real_actions() -> None:
    """Regression for mission 019eb17d: codex evidence must survive the gate."""
    stream = "\n".join(
        [
            _codex_item_line(
                {
                    "id": "item_3",
                    "type": "command_execution",
                    "command": "git diff -- email-analyse.html",
                    "aggregated_output": "+<!DOCTYPE html>",
                    "status": "completed",
                }
            ),
            _codex_item_line(
                {
                    "id": "item_57",
                    "type": "file_change",
                    "changes": [{"path": "email-analyse.html", "kind": "add"}],
                    "status": "completed",
                }
            ),
        ]
    )
    check = enforce_capability_honesty(
        user_request="Erstelle eine HTML-Datei und analysiere meine E-Mails",
        verdict=_approval_verdict(),
        worker_output=stream,
    )
    assert check.honesty_overridden is False
    assert check.verdict.verdict == "approve"
    assert check.tool_call_evidence


def test_gate_still_blocks_prose_only_email_claim() -> None:
    """The anti-fabrication contract is untouched: talk-only fails."""
    check = enforce_capability_honesty(
        user_request="Sende eine E-Mail an Max",
        verdict=_approval_verdict(),
        worker_output="I have sent the email to Max. Done!",
    )
    assert check.honesty_overridden is True
    assert check.verdict.verdict == "revise"


# --- empty-diff pre-gate: codex actions must defer to the LLM critic ---


@pytest.mark.asyncio
async def test_empty_diff_with_codex_actions_defers_to_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An MCP-only codex worker (e.g. send-mail, no file write) produces an
    empty worktree diff. The deterministic empty-diff veto must defer to
    the LLM critic when the codex log proves real actions — same contract
    the gate already honours for Claude ``tool_use`` records.
    """
    captured = _patch_direct(monkeypatch, stdout=_valid_verdict_json("approve"))
    codex_log = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "item_9",
                "type": "mcp_tool_call",
                "server": "gmail",
                "tool": "send_message",
                "status": "completed",
            },
        }
    )

    verdict = await CriticRunner().run(
        mission_prompt="Sende eine Status-Mail an Max",
        worker_diff="",
        worker_log=codex_log,
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert "argv" in captured, (
        "LLM critic was never spawned — empty-diff veto fired despite "
        "codex action evidence"
    )
    assert verdict.summary != (
        "Worker ran but the diff is empty; log claims are not ground truth. "
        "Deterministic revise."
    )
