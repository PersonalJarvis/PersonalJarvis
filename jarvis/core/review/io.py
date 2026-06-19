"""Filesystem layout for pipeline run artefacts (Phase 8.3).

Plan reference: §4.1, §AD-9 (filesystem IPC), §AD-11 (separate stores).

Layout:
    data/review/runs/<run_id>/
        ├ task.json          — original task, rubric, context
        ├ iter-1/
        │   ├ worker.out     — worker stdout (what the reviewer reads via Read)
        │   └ verdict.json   — reviewer verdict JSON
        ├ iter-2/
        │   └ ...
        └ final.json         — final result (best candidate + meta)

This class is the single place where the layout is defined. Spawners,
the pipeline, and UI routes (Phase 8.5) use only `RunDirectory` methods,
never raw path-string construction.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RunDirectory:
    """Encapsulates the layout of a pipeline run under `data/review/runs/<id>/`.

    `__init__` does not create the directory — the caller invokes `ensure()`
    when it is ready to write. Tests can therefore instantiate a `RunDirectory`
    without side effects and inspect it.
    """

    def __init__(self, root: Path | str, run_id: str) -> None:
        self._root = Path(root)
        self._run_id = run_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def path(self) -> Path:
        return self._root / self._run_id

    def ensure(self) -> RunDirectory:
        """Creates `path` if missing. Idempotent."""
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------------
    # task.json
    # ------------------------------------------------------------------

    @property
    def task_json_path(self) -> Path:
        return self.path / "task.json"

    def write_task(
        self,
        *,
        task: str,
        rubric_id: str,
        rubric_items: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        self.ensure()
        payload: dict[str, Any] = {
            "run_id": self._run_id,
            "task": task,
            "rubric_id": rubric_id,
        }
        if rubric_items is not None:
            payload["rubric_items"] = list(rubric_items)
        if extra:
            payload.update(extra)
        self.task_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.task_json_path

    # ------------------------------------------------------------------
    # iter-N/
    # ------------------------------------------------------------------

    def iter_dir(self, iteration: int) -> Path:
        if iteration < 1:
            raise ValueError("iteration must be >= 1")
        return self.path / f"iter-{iteration}"

    def worker_output_path(self, iteration: int) -> Path:
        return self.iter_dir(iteration) / "worker.out"

    def verdict_path(self, iteration: int) -> Path:
        return self.iter_dir(iteration) / "verdict.json"

    def write_worker_output(self, iteration: int, content: str) -> Path:
        target = self.worker_output_path(iteration)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_verdict(self, iteration: int, payload: dict[str, Any]) -> Path:
        target = self.verdict_path(iteration)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def read_worker_output(self, iteration: int) -> str:
        return self.worker_output_path(iteration).read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # final.json
    # ------------------------------------------------------------------

    @property
    def final_json_path(self) -> Path:
        return self.path / "final.json"

    def write_final(self, payload: dict[str, Any]) -> Path:
        self.ensure()
        self.final_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.final_json_path
