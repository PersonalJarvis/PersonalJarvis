"""The critic log summary must surface tool evidence + the worker's answer.

Regression for the 2026-05-24 GitHub-mission failure: inherited SessionStart
hook frames bloated the head of stream.jsonl, pushing the real github
tool_result + the answer past summarize_log's 4000-char cap, so the critic
concluded "zero github invocation" and failed a mission that had actually
succeeded.
"""
from __future__ import annotations

import json

from jarvis.missions.critic.log_summarizer import summarize_log


async def test_summary_surfaces_tool_evidence_past_char_cap() -> None:
    # 6 leading hook/init frames that alone blow the 4000-char budget
    noise = [
        json.dumps({"type": "system", "subtype": "init", "blob": "x" * 1500})
        for _ in range(6)
    ]
    real = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__github__get_me", "input": {}}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": '{"total_count":32}'}]}]}}),
        json.dumps({"type": "result",
                    "result": "Du hast 32 aktive Repositories.",
                    "subtype": "success"}),
    ]
    stream = "\n".join(noise + real)

    summary = await summarize_log(stream)

    assert "mcp__github__get_me" in summary, "critic must see the tool name"
    assert "Du hast 32 aktive Repositories" in summary, "critic must see the answer"


async def test_non_stream_log_still_summarized() -> None:
    # plain stderr text must keep working (head/tail/grep path)
    plain = "line one\nTraceback (most recent call last)\nValueError: boom\nlast line"
    summary = await summarize_log(plain)
    assert "ValueError" in summary
