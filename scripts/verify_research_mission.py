"""Live proof for the codex-worker web-search fix (2026-06-15).

Dispatches a REAL research mission that REQUIRES live web data through the real
worker -> critic pipeline (isolated short-path DB, live [brain.sub_jarvis]
provider). Before the fix the codex worker had no web access, fabricated current
events, and the critic rejected the hallucinations 3x -> critic_loop_exhausted
(live mission 019ecb56). With `-c tools.web_search=true` the worker can search
and cite real sources, so the critic can approve.

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_research_mission.py

Exit 0 only when the research mission reached APPROVED.
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
    "Use web search to research two or three major, real AI developments from "
    "2026 and write a short structured markdown report to a file named "
    "ai_news.md in the current working directory. Include a source URL for each "
    "item. Base every claim strictly on what the web search returns; do not "
    "invent anything."
)


def _saw_web_search(outputs_root: Path) -> bool:
    for p in outputs_root.rglob("stream.jsonl"):
        try:
            if "web_search" in p.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


async def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    work = Path(tempfile.mkdtemp(prefix="jvres-"))
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
        print(f"[..] sub_jarvis.provider={prov!r}; dispatching research mission…")
        mission_id = await manager.dispatch(prompt=PROMPT, language="en")
        final: MissionState = await kontrollierer.run_mission(mission_id)
        searched = _saw_web_search(outputs_root)
        tag = "OK" if final == MissionState.APPROVED else "FAIL"
        print(
            f"[{tag}] mission {mission_id[:8]} -> {final.value} "
            f"(worker performed web_search: {searched})"
        )
        if final != MissionState.APPROVED:
            for env in await manager.store.events_for_mission(mission_id):
                p = env.payload
                et = getattr(p, "event_type", "")
                if et in ("MissionFailed", "CriticVerdictReady"):
                    detail = getattr(p, "reason", None) or getattr(p, "summary", None)
                    print(f"    {et}: {str(detail)[:200]}")
        exit_code = 0 if final == MissionState.APPROVED else 1
    finally:
        await shutdown_missions(res)
        shutil.rmtree(work, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.stdout.flush()
    import os

    os._exit(code)
