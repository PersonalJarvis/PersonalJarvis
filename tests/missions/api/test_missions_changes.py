"""The per-file change ledger behind ``GET /api/missions/{id}/changes``."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.missions.diff_summary import read_mission_changes, summarize_unified_diff
from jarvis.missions.manager import MissionManager
from jarvis.ui.web.missions_routes import router as missions_router

PATCH = """\
diff --git a/jarvis/plugins/tool/gmail_deep_clean.py b/jarvis/plugins/tool/gmail_deep_clean.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/jarvis/plugins/tool/gmail_deep_clean.py
@@ -0,0 +1,3 @@
+import os
+
+print("hi")
diff --git a/README.md b/README.md
index 2222222..3333333 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-old line
+new line
 kept
diff --git a/old.txt b/new.txt
similarity index 90%
rename from old.txt
rename to new.txt
diff --git a/logo.png b/logo.png
new file mode 100644
Binary files /dev/null and b/logo.png differ
diff --desktop-action-evidence b/<desktop-launch-operations>
# verified-desktop-launch
+not a real line
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_summary_counts_every_kind_of_change_and_skips_pseudo_diffs() -> None:
    summary = summarize_unified_diff(PATCH)
    by_path = {f["path"]: f for f in summary["files"]}

    assert list(by_path) == [
        "jarvis/plugins/tool/gmail_deep_clean.py",
        "README.md",
        "new.txt",
        "logo.png",
        "gone.py",
    ]
    assert by_path["jarvis/plugins/tool/gmail_deep_clean.py"]["status"] == "added"
    assert by_path["jarvis/plugins/tool/gmail_deep_clean.py"]["additions"] == 3
    assert by_path["README.md"] == {
        "path": "README.md",
        "previous_path": None,
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "binary": False,
    }
    assert by_path["new.txt"]["status"] == "renamed"
    assert by_path["new.txt"]["previous_path"] == "old.txt"
    assert by_path["logo.png"]["binary"] is True
    assert by_path["gone.py"]["status"] == "deleted"
    assert by_path["gone.py"]["deletions"] == 2
    # The worker-authored evidence block contributes nothing.
    assert summary["additions"] == 4
    assert summary["deletions"] == 3
    assert summary["truncated_files"] is False


def test_read_mission_changes_prefers_the_final_patch_and_aggregates_tasks(
    tmp_path: Path,
) -> None:
    mid = "019fecaa-5a92-7360-a784-829281639cf6"
    mission_dir = tmp_path / f"mission_{mid[:13]}"
    task_a = mission_dir / "tasks" / "task-a" / "artifacts"
    task_b = mission_dir / "tasks" / "task-b" / "artifacts"
    task_a.mkdir(parents=True)
    task_b.mkdir(parents=True)
    (task_a / "diff.iter0.patch").write_text(
        "diff --git a/x b/x\n@@ -0,0 +1 @@\n+stale\n", encoding="utf-8"
    )
    (task_a / "diff.patch").write_text(PATCH, encoding="utf-8")
    (task_b / "diff.iter1.patch").write_text(
        "diff --git a/b.py b/b.py\nnew file mode 100644\n@@ -0,0 +1 @@\n+b\n", encoding="utf-8"
    )

    changes = read_mission_changes(tmp_path, mid)

    assert [t["task_id"] for t in changes["tasks"]] == ["task-a", "task-b"]
    assert changes["tasks"][0]["patch"] == "diff.patch"
    assert changes["tasks"][1]["patch"] == "diff.iter1.patch"
    assert len(changes["files"]) == 6
    assert changes["additions"] == 5
    assert changes["deletions"] == 3
    assert changes["truncated"] is False


def test_read_mission_changes_is_empty_for_a_missing_dir_or_bad_id(tmp_path: Path) -> None:
    assert read_mission_changes(tmp_path, "019fecaa-5a92-7360-a784-829281639cf6")["files"] == []
    assert read_mission_changes(tmp_path, "../escape")["files"] == []


@pytest_asyncio.fixture
async def manager(tmp_path: Path):
    mgr = MissionManager(tmp_path / "missions.db")
    await mgr.start()
    try:
        yield mgr
    finally:
        await mgr.stop()


async def test_changes_route_returns_the_ledger_for_a_known_mission(
    manager: MissionManager,
    tmp_path: Path,
) -> None:
    mid = await manager.dispatch(prompt="Build the thing.", language="en")
    artifacts = tmp_path / "outputs" / f"mission_{mid[:13]}" / "tasks" / "t1" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "diff.patch").write_text(PATCH, encoding="utf-8")

    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    app.state.outputs_root = tmp_path / "outputs"
    with TestClient(app) as client:
        ok = client.get(f"/api/missions/{mid}/changes")
        missing = client.get("/api/missions/00000000-0000-0000-0000-000000000000/changes")

    assert ok.status_code == 200
    body = ok.json()
    assert body["mission_id"] == mid
    assert len(body["files"]) == 5
    assert body["additions"] == 4
    assert missing.status_code == 404
