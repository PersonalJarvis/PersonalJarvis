"""Ad-hoc verification: the real CodexDirectWorker plans a trip WITHOUT
spawning a nested agent (multi_agent disabled) and produces actual output.

Proves the 2026-06-14 fix for the hung mission 019ec708 ("plan a trip from
London to Taiwan" → codex called spawn_agent('Hooke') + wait → frozen 7+ min).
Runs a REAL `codex exec` subprocess (~1 min, small ChatGPT-subscription cost).
Exit 0 = produced output + no spawn_agent; exit 1 = recursion/no output.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from jarvis.missions.workers.codex_direct_worker import (  # noqa: E402
    CodexDirectWorker,
    _build_codex_direct_cmd,
)
from jarvis.missions.workers.stream_consumer import ClaudeResult  # noqa: E402

PROMPT = "Plan a trip from London to Taiwan."


class _Job:
    def assign(self, pid: int) -> None:  # noqa: D401
        pass


async def main() -> int:
    # 1. The argv must disable the multi-agent collaboration tools.
    cmd = _build_codex_direct_cmd(worktree=Path("."), model=None)
    if "multi_agent" not in cmd:
        print("[FAIL] argv does not disable multi_agent:", cmd)
        return 1
    print("[OK] argv disables multi_agent / multi_agent_v2")

    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "wt"
        wt.mkdir()
        logs = Path(tmp) / "logs"
        final: ClaudeResult | None = None
        events = 0
        # Real env so codex finds USERPROFILE/HOME (-> ~/.codex/auth.json) + PATH.
        # The worker's _build_codex_env still drops CODEX_HOME / OPENAI_API_KEY.
        async for ev in CodexDirectWorker().spawn(
            PROMPT,
            worktree=wt,
            env=dict(os.environ),
            job=_Job(),
            worker_id="verify",
            log_dir=logs,
            timeout_s=180.0,
            first_output_timeout_s=150.0,
        ):
            events += 1
            if isinstance(ev, ClaudeResult):
                final = ev

        stream_path = logs / "stream.jsonl"
        stream = (
            stream_path.read_text(encoding="utf-8", errors="replace")
            if stream_path.exists() else ""
        )
        stderr_path = logs / "stderr.log"
        if stderr_path.exists():
            err = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            if err:
                print(f"--- stderr.log ({len(err)} chars) ---")
                print(err[:600])
        # 2. The worker must NOT have spawned a nested agent.
        if "spawn_agent" in stream or "collab_tool_call" in stream:
            print("[FAIL] worker spawned a NESTED agent (multi_agent still active)")
            return 1
        print("[OK] no spawn_agent / collab_tool_call in the stream")

        # 3. The worker must have produced a substantive output.
        if final is None or final.is_error:
            print(f"[FAIL] no successful terminal result: {final!r}")
            return 1
        answer = (final.result or "").strip()
        if len(answer) < 80:
            print(f"[FAIL] output too short ({len(answer)} chars): {answer!r}")
            return 1
        secs = (final.duration_ms or 0) / 1000
        print(f"[OK] produced output ({len(answer)} chars, {events} events)")
        print(f"[TIME] worker wall-clock: {secs:.0f}s "
              f"(xhigh baseline on live mission 019ec742 was ~450s/run)")
        if "model_reasoning_effort=medium" in " ".join(cmd):
            print("[OK] reasoning effort capped to medium")
        print("--- first 400 chars of the plan ---")
        print(answer[:400])
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
