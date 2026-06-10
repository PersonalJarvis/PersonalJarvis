"""Five-layer parity guard for ``WorkerKilled.reason`` (docs/anti-drift-three-layer.md).

The reason vocabulary spans Python (`events.py` Literal) ↔ TypeScript
(`frontend/src/types/missions.ts` `WorkerKilledReason`). They silently drifted
(the TS union was missing `path_guard`, and `worker_error` was added Python-side
to replace the dishonest `"user"` mislabel). This test locks them so the
BUG-008 enum-drift class cannot recur.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from jarvis.missions.events import WorkerKilled


def _python_reasons() -> set[str]:
    return set(get_args(WorkerKilled.model_fields["reason"].annotation))


def _ts_reasons() -> set[str]:
    ts = (
        Path(__file__).resolve().parents[2]
        / "jarvis" / "ui" / "web" / "frontend" / "src" / "types" / "missions.ts"
    )
    text = ts.read_text(encoding="utf-8")
    m = re.search(r"export type WorkerKilledReason\s*=([^;]+);", text)
    assert m, "WorkerKilledReason union not found in missions.ts"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_worker_killed_reason_python_ts_parity() -> None:
    py, ts = _python_reasons(), _ts_reasons()
    assert py == ts, (
        f"WorkerKilled.reason drift — python-only={py - ts}, ts-only={ts - py}"
    )


def test_worker_error_replaces_user_mislabel_present() -> None:
    assert "worker_error" in _python_reasons()
