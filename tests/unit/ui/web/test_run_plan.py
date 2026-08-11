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
