"""Unit tests for the run step-timeline reader (`jarvis.ui.web.run_plan`).

The Visualization node graph is only as honest as this parser: a tool call
must become `done`/`failed` ONLY via its correlated result frame, a truncated
stream must say so, and a run without a stream must return the former stub's
`{plan: None, steps: []}` contract unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.outputs_routes import router as outputs_router
from jarvis.ui.web.run_plan import MAX_STEPS, build_run_plan


def _stream_dir(root: Path, task_id: str = "task-1") -> Path:
    d = root / "tasks" / task_id / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tool_use(tid: str, name: str, tool_input: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tid, "name": name, "input": tool_input}
                ],
            },
        }
    )


def _tool_result(tid: str, content: str, *, is_error: bool = False) -> str:
    blk: dict = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error:
        blk["is_error"] = True
    return json.dumps({"type": "user", "message": {"role": "user", "content": [blk]}})


def _result(text: str) -> str:
    return json.dumps({"type": "result", "result": text})


def _assistant_blocks(*blocks: dict) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}
    )


def _thinking(text: str) -> dict:
    return {"type": "thinking", "thinking": text}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def test_no_stream_keeps_stub_contract(tmp_path: Path) -> None:
    assert build_run_plan(tmp_path) == {"plan": None, "steps": []}


def test_empty_stream_keeps_stub_contract(tmp_path: Path) -> None:
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("", encoding="utf-8")
    assert build_run_plan(tmp_path) == {"plan": None, "steps": []}


def test_steps_carry_correlated_status_and_final_answer(tmp_path: Path) -> None:
    lines = [
        _tool_use("t1", "Bash", {"command": "python plot.py"}),
        _tool_result("t1", "wrote chart.png"),
        _tool_use("t2", "Write", {"file_path": "report/chart.png", "content": "…"}),
        _tool_result("t2", "<tool_use_error>disk full</tool_use_error>", is_error=False),
        _tool_use("t3", "Read", {"file_path": "notes.md"}),  # no result frame
        _result("All done: one chart rendered."),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    payload = build_run_plan(tmp_path, utterance="Draw a chart")

    assert payload["plan"]["vision"] == "Draw a chart"
    assert payload["plan"]["total_steps"] == 3
    assert payload["final_answer"] == "All done: one chart rendered."
    assert payload["truncated"] is False

    by_tool = {s["tool_name"]: s for s in payload["steps"]}
    assert by_tool["Bash"]["status"] == "done"
    assert by_tool["Bash"]["name"] == "python plot.py"
    assert by_tool["Bash"]["output"] == "wrote chart.png"
    # The <tool_use_error> marker counts as failure even without is_error.
    assert by_tool["Write"]["status"] == "failed"
    assert by_tool["Write"]["writes"] == ["report/chart.png"]
    # Anti-hearsay: a call whose result never arrived is skipped, not done.
    assert by_tool["Read"]["status"] == "skipped"


def test_thinking_between_actions_collapses_into_one_reasoning_step(tmp_path: Path) -> None:
    lines = [
        _assistant_blocks(_thinking("Plan: run the script."), _thinking("Then verify.")),
        _tool_use("t1", "Bash", {"command": "python plot.py"}),
        _tool_result("t1", "ok"),
        _assistant_blocks(_thinking("Looks good, write it out.")),
        _tool_use("t2", "Write", {"file_path": "out.md", "content": "…"}),
        _tool_result("t2", "written"),
        _result("Done."),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("\n".join(lines), encoding="utf-8")

    payload = build_run_plan(tmp_path)

    kinds = [(s.get("kind"), s.get("tool_name")) for s in payload["steps"]]
    assert kinds == [
        ("reasoning", None),
        ("tool", "Bash"),
        ("reasoning", None),
        ("tool", "Write"),
    ]
    # Two consecutive thinking blocks became ONE step, in reading order.
    first = payload["steps"][0]
    assert first["name"] == "Plan: run the script. Then verify."
    assert first["status"] == "done"
    assert first["task_key"] == "task-1"


def test_trailing_text_is_the_answer_never_a_reasoning_step(tmp_path: Path) -> None:
    lines = [
        _tool_use("t1", "Bash", {"command": "ls"}),
        _tool_result("t1", "ok"),
        # Thinking after the last action still counts as a step; the final
        # text does not — it IS the answer, and must not appear twice.
        _assistant_blocks(_thinking("Summarise for the user."), _text("Here is the list.")),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("\n".join(lines), encoding="utf-8")

    payload = build_run_plan(tmp_path)

    assert [s.get("kind") for s in payload["steps"]] == ["tool", "reasoning"]
    assert payload["steps"][1]["name"] == "Summarise for the user."
    assert payload["final_answer"] == "Here is the list."


def test_spawn_tools_carry_the_spawn_kind(tmp_path: Path) -> None:
    lines = [
        _tool_use("t1", "Task", {"prompt": "Audit shard A", "description": "Audit shard A"}),
        _tool_result("t1", "spawned"),
        _tool_use("t2", "Bash", {"command": "echo hi"}),
        _tool_result("t2", "hi"),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("\n".join(lines), encoding="utf-8")

    payload = build_run_plan(tmp_path)

    by_tool = {s["tool_name"]: s for s in payload["steps"]}
    assert by_tool["Task"]["kind"] == "spawn"
    assert by_tool["Bash"]["kind"] == "tool"


def test_all_actions_failed_verdict_ignores_reasoning_steps(tmp_path: Path) -> None:
    lines = [
        _assistant_blocks(_thinking("Try the deploy.")),
        _tool_use("t1", "Bash", {"command": "npm run deploy"}),
        _tool_result("t1", "boom", is_error=True),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("\n".join(lines), encoding="utf-8")

    payload = build_run_plan(tmp_path)

    # The reasoning step is "done", but the verdict is judged over actions.
    assert payload["plan"]["status"] == "failed"


def test_codex_stream_is_normalized_before_walking(tmp_path: Path) -> None:
    lines = [
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "ls -la",
                    "aggregated_output": "total 4",
                    "exit_code": 0,
                },
            }
        ),
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}}
        ),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    payload = build_run_plan(tmp_path)

    assert [s["tool_name"] for s in payload["steps"]] == ["Bash"]
    assert payload["steps"][0]["status"] == "done"
    assert payload["final_answer"] == "Done."


def test_gemini_stream_json_walks_into_reasoning_and_tool_steps(tmp_path: Path) -> None:
    """A gemini `--output-format stream-json` archive draws the full story:
    commentary → reasoning step, tool_use/tool_result → correlated tool step,
    trailing text → the final answer (never duplicated as a step)."""
    ts = "2026-08-12T17:00:00.000Z"
    lines = [
        json.dumps({"type": "init", "timestamp": ts, "session_id": "s", "model": "gemini"}),
        json.dumps(
            {
                "type": "message",
                "timestamp": ts,
                "role": "assistant",
                "content": "I will write the report now.",
                "delta": True,
            }
        ),
        json.dumps(
            {
                "type": "tool_use",
                "timestamp": ts,
                "tool_name": "write_file",
                "tool_id": "g1",
                "parameters": {"file_path": "report.md", "content": "x"},
            }
        ),
        json.dumps(
            {
                "type": "tool_result",
                "timestamp": ts,
                "tool_id": "g1",
                "status": "success",
                "output": "Wrote report.md",
            }
        ),
        json.dumps(
            {
                "type": "message",
                "timestamp": ts,
                "role": "assistant",
                "content": "Done — the report is ready.",
                "delta": True,
            }
        ),
        json.dumps(
            {"type": "result", "timestamp": ts, "status": "success", "stats": {}}
        ),
    ]
    (_stream_dir(tmp_path) / "stream.jsonl").write_text("\n".join(lines), encoding="utf-8")

    payload = build_run_plan(tmp_path)

    kinds = [(s.get("kind"), s.get("tool_name")) for s in payload["steps"]]
    assert kinds == [("reasoning", None), ("tool", "write_file")]
    assert payload["steps"][0]["name"] == "I will write the report now."
    assert payload["steps"][1]["status"] == "done"
    assert payload["steps"][1]["writes"] == ["report.md"]
    assert payload["final_answer"] == "Done — the report is ready."


def test_step_cap_reports_dropped_count(tmp_path: Path) -> None:
    lines: list[str] = []
    for i in range(MAX_STEPS + 25):
        lines.append(_tool_use(f"t{i}", "Bash", {"command": f"step {i}"}))
        lines.append(_tool_result(f"t{i}", "ok"))
    (_stream_dir(tmp_path) / "stream.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    payload = build_run_plan(tmp_path)

    assert len(payload["steps"]) == MAX_STEPS
    assert payload["dropped_steps"] == 25
    assert payload["plan"]["total_steps"] == MAX_STEPS + 25


def test_multiple_tasks_walk_in_order_with_unique_ids(tmp_path: Path) -> None:
    for task, cmd in (("a-task", "first"), ("b-task", "second")):
        (_stream_dir(tmp_path, task) / "stream.jsonl").write_text(
            "\n".join(
                [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "ok")]
            ),
            encoding="utf-8",
        )

    payload = build_run_plan(tmp_path)

    assert [s["name"] for s in payload["steps"]] == ["first", "second"]
    ids = [s["step_id"] for s in payload["steps"]]
    assert len(ids) == len(set(ids))


def test_plan_route_serves_reconstructed_steps(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(outputs_router)
    app.state.outputs_root = tmp_path

    run = tmp_path / "mission_deadbeef"
    (_stream_dir(run) / "stream.jsonl").write_text(
        "\n".join(
            [
                _tool_use("t1", "Bash", {"command": "echo hi"}),
                _tool_result("t1", "hi"),
                _result("Said hi."),
            ]
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    payload = client.get("/api/outputs/mission_deadbeef/plan").json()

    assert payload["plan"]["plan_id"] == "mission_deadbeef"
    assert payload["steps"][0]["tool_name"] == "Bash"
    assert payload["final_answer"] == "Said hi."
