"""Unit tests for the mission node-graph page (model, layout honesty, escaping).

The renderer bakes worker- and user-authored strings into a page served in the
app origin, so the escape tests here are security guards, not cosmetics. The
overflow tests guard the honesty rule: a collapsed file must read as
"collapsed", never as "not produced".
"""
from __future__ import annotations

from jarvis.ui.web.mission_graph import (
    BRAND,
    MAX_FILES_PER_STEP,
    build_mission_graph,
    render_mission_graph_html,
)


def _files(step_id: str, names: list[str]) -> list[dict[str, object]]:
    return [
        {"path": f"tasks/{step_id}/artifacts/files/{name}", "size": 1000 + i}
        for i, name in enumerate(names)
    ]


def _build(**overrides: object):
    kwargs: dict[str, object] = {
        "utterance": "Build a city guide",
        "status": "success",
        "summary": None,
        "duration_s": 120.0,
        "task_ids": ["019fed06-19e8"],
        "files": _files("019fed06-19e8", ["guide.html", "map.png"]),
    }
    kwargs.update(overrides)
    return build_mission_graph("mission_019fed06-19df", **kwargs)  # type: ignore[arg-type]


# --- Model -------------------------------------------------------------------


def test_files_group_under_their_step():
    data = _build()
    assert len(data.steps) == 1
    assert [f.path for f in data.steps[0].files] == ["guide.html", "map.png"]


def test_step_without_files_stays_visible():
    data = _build(task_ids=["019fed06-19e8", "019fed06-ffff"])
    assert {s.step_id for s in data.steps} == {"019fed06-19e8", "019fed06-ffff"}


def test_worktree_style_step_id_becomes_readable_label():
    data = _build(task_ids=["01__refactor-router"], files=[])
    assert data.steps[0].label == "01 · Refactor router"


def test_uuid_step_id_becomes_step_number():
    data = _build()
    assert data.steps[0].label == "Step 1"


def test_non_archive_paths_are_skipped_not_guessed():
    data = _build(files=[{"path": "reflections.md", "size": 10}])
    assert all(not s.files for s in data.steps)


def test_unknown_status_normalizes_to_unknown():
    assert _build(status="weird").status == "unknown"


def test_plan_steps_aggregate_tool_calls_and_failures():
    plan_steps = [
        {"step_id": "019fed06-19e8:0", "status": "done"},
        {"step_id": "019fed06-19e8:1", "status": "failed"},
        {"step_id": "019fed06-19e8:2", "status": "done"},
    ]
    data = _build(plan_steps=plan_steps)
    assert data.steps[0].tool_calls == 3
    assert data.steps[0].failed_calls == 1


# --- Rendering ---------------------------------------------------------------


def test_page_uses_brand_theme():
    out = render_mission_graph_html(_build())
    assert BRAND["bg"] in out  # matte-black ground
    assert BRAND["primary"] in out  # signal-yellow accent


def test_page_has_no_script_anywhere():
    # Served under the no-script CSP — the page must not need JS to render.
    out = render_mission_graph_html(_build())
    assert "<script" not in out.lower()


def test_nodes_and_edges_render():
    out = render_mission_graph_html(_build())
    assert 'class="node node-mission"' in out
    assert 'class="node node-step"' in out
    assert out.count('class="node node-file"') == 2
    # One track per connection: mission→step plus step→each file.
    assert out.count('<path class="edge"') == 3


def test_utterance_and_filenames_are_escaped():
    data = _build(
        utterance='<script>alert(1)</script>',
        files=_files("019fed06-19e8", ['<img src=x onerror=alert(1)>.html']),
    )
    out = render_mission_graph_html(data)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert "<img src=x" not in out


def test_overflow_states_the_hidden_count():
    many = _files("019fed06-19e8", [f"page{i}.md" for i in range(MAX_FILES_PER_STEP + 4)])
    out = render_mission_graph_html(_build(files=many))
    assert out.count('class="node node-file"') == MAX_FILES_PER_STEP
    assert "+4 more files" in out


def test_empty_mission_renders_with_honest_note():
    out = render_mission_graph_html(_build(task_ids=[], files=[]))
    assert "archived no steps yet" in out
    assert 'class="node node-mission"' in out


def test_failed_calls_surface_as_step_badge():
    plan_steps = [{"step_id": "019fed06-19e8:0", "status": "failed"}]
    out = render_mission_graph_html(_build(plan_steps=plan_steps))
    assert "1 failed" in out


def test_running_mission_gets_flowing_tracks_and_settled_does_not():
    running = render_mission_graph_html(_build(status="running"))
    settled = render_mission_graph_html(_build(status="success"))
    assert "track-flow" in running
    assert "track-flow" not in settled
