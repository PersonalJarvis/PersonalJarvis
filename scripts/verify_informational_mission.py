"""Live end-to-end proof for the provider-agnostic evidence fix (2026-06-15).

Dispatches a REAL informational mission ("which city for a first trip to
Australia?") through the real worker -> critic pipeline against an ISOLATED DB,
using the LIVE ``[brain.sub_jarvis]`` provider. Before the fix, an informational
codex/gemini answer was invisible to the empty-diff gate -> 3x deterministic
revise -> ``critic_loop_exhausted`` (live mission 019ec761). This asserts the
mission reaches APPROVED and reports which backend actually ran (parsed from the
worker ``stream.jsonl``), so the proof is honest about codex-vs-fallback.

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_informational_mission.py

Exit 0 only when the informational mission reached APPROVED.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.init import bootstrap_missions, shutdown_missions  # noqa: E402
from jarvis.missions.state_machine import MissionState  # noqa: E402

PROMPT = (
    "Which Australian city would you recommend for a first visit, and why? "
    "Reply with a short paragraph."
)


def _detect_backend(outputs_root: Path) -> str:
    for p in outputs_root.rglob("stream.jsonl"):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = txt[:8000]
        if '"thread.started"' in head or '"item.completed"' in head:
            return "codex"
        if '"type":"assistant"' in head or '"type": "assistant"' in head:
            return "claude"
        if head.strip():
            return "gemini/plain-text"
    return "unknown (no stream.jsonl found)"


async def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    # Short system-temp root: the worktree path (root + timestamp-slug-hash +
    # tasks/<uuid>/workspace) must stay under the 200-char Phase-6 cap, so a
    # deep repo-nested path overflows. Mirror verify_submission_provider_fix.py.
    work = Path(tempfile.mkdtemp(prefix="jvinfo-"))
    outputs_root = work / "o"

    res = await bootstrap_missions(
        db_path=work / "missions.db",
        isolation_root=outputs_root,
        repo_root=repo_root,
        recover_missions=False,
    )
    manager = res["manager"]
    kontrollierer = res["kontrollierer"]
    exit_code = 1
    try:
        from jarvis.core.config import load_config

        cfg = load_config()
        prov = getattr(getattr(cfg.brain, "sub_jarvis", None), "provider", None)
        print(f"[..] sub_jarvis.provider={prov!r}; dispatching informational mission…")
        mission_id = await manager.dispatch(prompt=PROMPT, language="en")
        final: MissionState = await kontrollierer.run_mission(mission_id)
        backend = _detect_backend(outputs_root)
        tag = "OK" if final == MissionState.APPROVED else "FAIL"
        print(
            f"[{tag}] mission {mission_id[:8]} -> {final.value} "
            f"(worker backend: {backend})"
        )
        if final != MissionState.APPROVED:
            # Diagnostics: surface the terminal reason + the critic's last words.
            for env in await manager.store.events_for_mission(mission_id):
                p = env.payload
                et = getattr(p, "event_type", "")
                if et in ("MissionFailed", "CriticVerdictReady", "WorkerCorrectionRequired"):
                    detail = (
                        getattr(p, "reason", None)
                        or getattr(p, "summary", None)
                        or getattr(p, "correction_instruction", None)
                    )
                    print(f"    {et}: {str(detail)[:220]}")
        exit_code = 0 if final == MissionState.APPROVED else 1
    finally:
        await shutdown_missions(res)
        shutil.rmtree(work, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    code = asyncio.run(main())
    # google-genai gRPC clients leak non-daemon threads that hang process exit
    # (project memory: grpc-zombie-threads). Hard-exit after the verdict prints.
    sys.stdout.flush()
    import os

    os._exit(code)
