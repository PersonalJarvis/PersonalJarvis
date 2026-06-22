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
from jarvis.missions.critic.runner import _request_is_messaging_action
from jarvis.missions.stream_evidence import diff_has_action_evidence
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


# --- prose/CLI workers (agy/Antigravity, gemini --yolo): the git diff is evidence ---
#
# Live mission 019eefda-7e5f (2026-06-22): the user picked Antigravity as the
# subagent provider; agy REALLY wrote an 80 KB index.html into the worktree, but
# it emits only narrative PROSE over its PTY ("I will create index.html…"), never
# a machine-readable tool_use frame. `_extract_tool_call_evidence` is therefore
# always empty for agy/gemini, so the honesty gate overrode every iteration to
# "made no tool call" -> 3x revise -> 13-20 min -> the user gave up ("Ewigkeiten").
# A real worktree diff is the GROUND-TRUTH-RULE's own source of truth, so it must
# satisfy the gate exactly as a tool_use frame does.

_AGY_PROSE = (
    "I will analyze the workspace directory to understand its layout.\n"
    "I will create index.html with a polished market dashboard.\n"
    "I have created the file and the task is complete."
)

_REAL_GIT_DIFF = (
    "diff --git a/index.html b/index.html\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/index.html\n"
    "@@ -0,0 +1,2 @@\n"
    "+<!DOCTYPE html>\n"
    "+<html><body><h1>Market Dashboard</h1></body></html>\n"
)


def test_diff_has_action_evidence_recognises_real_markers() -> None:
    assert diff_has_action_evidence(_REAL_GIT_DIFF) is True
    assert diff_has_action_evidence("diff --external-target b/out/x.html\n+<html>") is True
    assert diff_has_action_evidence("diff --command-evidence b/<ops>\n+main -> main") is True
    assert (
        diff_has_action_evidence("diff --desktop-action-evidence b/<launch>\n+ok") is True
    )


def test_diff_has_action_evidence_rejects_empty_or_prose() -> None:
    assert diff_has_action_evidence("") is False
    assert diff_has_action_evidence("   \n  \n") is False
    # prose that merely mentions a diff is not a diff
    assert diff_has_action_evidence("I produced a diff for index.html") is False


def test_request_is_messaging_action() -> None:
    # real send actions: verb + messaging noun
    assert _request_is_messaging_action("Sende eine E-Mail an Max") is True
    assert _request_is_messaging_action("Schick eine WhatsApp an Lisa") is True
    assert _request_is_messaging_action("Tweet this update") is True
    assert _request_is_messaging_action("reply to the message from Tom") is True
    # artefact tasks that only MENTION the topic must NOT be misclassified
    assert _request_is_messaging_action(
        "Erstelle einen HTML-Report über meine E-Mails"
    ) is False
    assert _request_is_messaging_action("Mach eine HTML-Datei zum Aktienmarkt") is False


def _force_requires_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin requires_evidence=True so the test exercises the diff-evidence branch
    regardless of the (empty in unit tests) capability registry."""
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_capability_requires_evidence",
        lambda _req: (True, "test.write-artefact"),
    )


def test_gate_credits_worktree_diff_for_prose_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agy/gemini write real files but emit only prose — the git diff is the
    ground-truth artefact and must satisfy the honesty gate (regression for
    live mission 019eefda, 2026-06-22: agy wrote an 80 KB index.html, the gate
    still said 'made no tool call' and burned all three critic loops)."""
    _force_requires_evidence(monkeypatch)
    check = enforce_capability_honesty(
        user_request="Mach eine visuell ansprechende HTML-Datei zum Aktienmarkt",
        verdict=_approval_verdict(),
        worker_output=_AGY_PROSE,  # NO tool_use frames at all
        worker_diff=_REAL_GIT_DIFF,
    )
    assert check.honesty_overridden is False
    assert check.verdict.verdict == "approve"
    assert check.tool_call_evidence  # the filesystem change is credited


def test_gate_still_blocks_prose_worker_with_empty_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-hallucination contract intact: a prose claim with NO diff AND NO
    tool frame still fails, even for a CLI worker."""
    _force_requires_evidence(monkeypatch)
    check = enforce_capability_honesty(
        user_request="Mach eine visuell ansprechende HTML-Datei zum Aktienmarkt",
        verdict=_approval_verdict(),
        worker_output=_AGY_PROSE,
        worker_diff="",  # nothing written
    )
    assert check.honesty_overridden is True
    assert check.verdict.verdict == "revise"


def test_gate_messaging_action_ignores_file_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file write does NOT prove a message was sent: a real send action keeps
    requiring a messaging tool call even when a diff exists (the diff-evidence
    credit must not re-open the 'I have sent the email' hallucination)."""
    _force_requires_evidence(monkeypatch)
    check = enforce_capability_honesty(
        user_request="Sende eine E-Mail an Max mit der Zusammenfassung",
        verdict=_approval_verdict(),
        worker_output=_AGY_PROSE,
        worker_diff=_REAL_GIT_DIFF,  # wrote a file, but that's not a sent email
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
