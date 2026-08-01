"""Provider-agnostic evidence extraction: the critic gates must SEE a codex or
gemini worker's real work and final answer, not only claude's stream-json shape.

Root cause (2026-06-15 deep-dive, live mission 019ec761 + DB forensics): every
extractor in ``jarvis.missions.stream_evidence`` parsed ONLY claude frames
(``type==assistant/user/result`` with ``tool_use``/``tool_result`` blocks). A
codex worker writes ``item.completed`` frames and a gemini worker writes plain
text (``--output-format text``) — both invisible. Consequence: a codex/gemini
INFORMATIONAL task produced an answer the gate could not read, so
``readonly_answer`` returned ``None``, the empty-diff veto fired, and the mission
burned 3 deterministic ``revise`` loops -> ``critic_loop_exhausted``. The
maintainer's mandate is that missions work reliably with EVERY brain provider,
so this pins the multi-format contract.

The hallucination guard (a bare "done" claim with no diff and no tools on a
DO-task stays vetoed) must remain intact across all formats.
"""
from __future__ import annotations

import json

from jarvis.missions.stream_evidence import (
    capability_refusal_answer,
    extract_stream_evidence,
    extract_verified_commands,
    extract_verified_desktop_actions,
    extract_write_targets,
    readonly_answer,
)


# --- codex `exec --json` frame builders (the real on-disk shapes) -----------
def _codex_agent(text: str) -> str:
    return json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
    )


def _codex_file(path: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "file_change", "changes": [{"path": path}]},
        }
    )


def _codex_cmd(cmd: str, *, output: str = "", exit_code: str = "0") -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": cmd,
                "aggregated_output": output,
                "exit_code": exit_code,
            },
        }
    )


INFO_PROMPT = "Which city would you recommend for a trip to Australia?"
DO_PROMPT = "Create a file report.md with the analysis."


# --- codex: final answer + tool evidence ------------------------------------
def test_codex_agent_message_is_final_answer() -> None:
    ev = extract_stream_evidence(_codex_agent("Sydney is the best first-trip city."))
    assert "Sydney" in ev.final_answer


def test_codex_file_change_credits_write_and_tool_evidence() -> None:
    stream = _codex_file("report.md")
    assert "report.md" in extract_write_targets(stream)
    assert extract_stream_evidence(stream).has_tool_evidence


def test_codex_command_execution_credits_mutating_push() -> None:
    stream = _codex_cmd("git push origin main", output="main -> main", exit_code="0")
    creds = extract_verified_commands(stream)
    assert any("git push" in cmd for cmd, _ in creds)


def test_codex_failed_command_is_not_credited() -> None:
    stream = _codex_cmd("git push origin main", output="rejected", exit_code="1")
    assert extract_verified_commands(stream) == ()


def test_codex_desktop_launch_is_credited() -> None:
    stream = _codex_cmd("start chrome", output="", exit_code="0")
    actions = extract_verified_desktop_actions(stream)
    assert any("start chrome" in cmd for cmd, _ in actions)


# --- codex: informational answer is spoken back (THE 019ec761 fix) ----------
def test_codex_informational_answer_is_spoken_back() -> None:
    stream = _codex_agent("I recommend Melbourne for its culture and coffee scene.")
    answer = readonly_answer("", stream, prompt=INFO_PROMPT)
    assert answer is not None
    assert "Melbourne" in answer


def test_codex_honest_refusal_is_surfaced() -> None:
    stream = _codex_agent(
        "I'm not able to access travel booking systems, so I can't book the trip."
    )
    refusal = capability_refusal_answer(stream, prompt="Book me a trip to Tokyo")
    assert refusal is not None
    assert "not able to" in refusal.lower()


# --- gemini: plain-text (`--output-format text`) -----------------------------
def test_gemini_plain_text_answer_is_extracted() -> None:
    stream = (
        "Here is my recommendation:\n"
        "Sydney offers the best balance for a first visit."
    )
    ev = extract_stream_evidence(stream)
    assert "Sydney" in ev.final_answer


def test_gemini_plain_text_informational_is_spoken_back() -> None:
    stream = "Sydney offers the best balance for a first visit to Australia."
    answer = readonly_answer("", stream, prompt=INFO_PROMPT)
    assert answer is not None
    assert "Sydney" in answer


# --- regression: claude stream still parses byte-for-byte the same -----------
def test_claude_stream_unchanged() -> None:
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Write",
                                "input": {"file_path": "a.md"},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": "ok",
                            }
                        ]
                    },
                }
            ),
            json.dumps({"type": "result", "result": "done writing a.md"}),
        ]
    )
    assert "a.md" in extract_write_targets(stream)
    assert extract_stream_evidence(stream).final_answer == "done writing a.md"


# --- hallucination guard intact across formats ------------------------------
def test_codex_hallucinated_done_on_do_task_is_not_approved() -> None:
    # Worker CLAIMS a file but emitted no file_change frame and there is no diff.
    stream = _codex_agent("I created report.md with the full analysis.")
    assert readonly_answer("", stream, prompt=DO_PROMPT) is None
    assert capability_refusal_answer(stream, prompt=DO_PROMPT) is None


def test_gemini_hallucinated_done_on_do_task_is_not_approved() -> None:
    stream = "I have created report.md exactly as requested."
    assert readonly_answer("", stream, prompt=DO_PROMPT) is None
