"""Headless E2E verification of the xai/sonnet fix.

Spawns the SubJarvisWorker directly (no Mission-Manager / Kontrollierer
layer in between) with a tiny prompt, then inspects:

    - the worker yielded ClaudeSystemInit + ClaudeResult
    - the model arg in the spawned argv is NOT "xai/sonnet" (the bug)
    - the spawned argv carries "xai/grok-4.3" (the config chain)
    - the workspace contains the file the prompt asked for
    - stderr.log contains no FailoverError

Two missions are run with different goals to match the goal spec.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Project on sys.path
PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))

from jarvis.missions.isolation.job_object import AlwaysOpenJobObject
from jarvis.missions.workers.subjarvis_worker import SubJarvisWorker


def _build_env() -> dict[str, str]:
    """Minimal env: XAI_API_KEY for grok + system tools."""
    from jarvis.core.config import get_secret

    grok = get_secret("grok_api_key", env_fallback="GROK_API_KEY")
    env = {
        # tools the openclaw CLI needs to spawn (path lookups, etc.)
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
        "HOMEPATH": os.environ.get("HOMEPATH", "\\Users\\Administrator"),
    }
    if grok:
        env["XAI_API_KEY"] = grok
        env["GROK_API_KEY"] = grok
    return env


async def _run_one_mission(
    label: str,
    prompt: str,
    artefact_check,
) -> dict[str, Any]:
    mission_dir = PROJECT.parent / "sub-agents-outputs" / f"e2e_verify_{label}_{uuid.uuid4().hex[:8]}"
    workspace = mission_dir / "workspace"
    logs = mission_dir / "logs"
    workspace.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    env = _build_env()
    env["OPENCLAW_STATE_DIR"] = str(mission_dir / "openclaw_state")

    worker = SubJarvisWorker()
    started = time.time()
    events = []

    # NOTE: We pass model="sonnet" on purpose — that's the exact
    # Decomposer-default value that produced the bug. The fix must filter
    # it out and use [brain.sub_jarvis] from jarvis.toml (= grok-4.3) instead.
    try:
        async for ev in worker.spawn(
            prompt,
            worktree=workspace,
            env=env,
            job=AlwaysOpenJobObject(),
            worker_id=f"e2e-{label}",
            log_dir=logs,
            model="sonnet",  # the trapdoor — must be filtered
            timeout_s=300.0,
        ):
            events.append(type(ev).__name__)
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "ok": False,
            "reason": f"worker spawn raised: {exc!r}",
            "duration_s": time.time() - started,
            "events": events,
            "mission_dir": str(mission_dir),
        }

    duration_s = time.time() - started

    # Inspect logs
    stderr_path = logs / "stderr.log"
    stream_path = logs / "stream.jsonl"
    stderr_txt = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    stream_txt = stream_path.read_text(encoding="utf-8", errors="replace") if stream_path.exists() else ""

    bad_failover = "FailoverError" in stderr_txt or "xai/sonnet" in stderr_txt
    artefact_ok, artefact_detail = artefact_check(workspace)

    return {
        "label": label,
        "events": events,
        "duration_s": round(duration_s, 1),
        "stderr_clean": not bad_failover,
        "stderr_snippet": stderr_txt[:500] if stderr_txt else "",
        "stream_has_init_and_result": (
            "ClaudeSystemInit" in events and "ClaudeResult" in events
        ),
        "artefact_ok": artefact_ok,
        "artefact_detail": artefact_detail,
        "mission_dir": str(mission_dir),
    }


def _check_hello_py(workspace: Path) -> tuple[bool, str]:
    """Mission 1: hello.py must exist and print 'hello world'."""
    f = workspace / "hello.py"
    if not f.exists():
        return False, f"missing {f}"
    content = f.read_text(encoding="utf-8")
    if "hello world" not in content.lower():
        return False, f"content does not contain 'hello world': {content[:200]!r}"
    if "print" not in content:
        return False, f"no print() call: {content[:200]!r}"
    return True, f"hello.py {f.stat().st_size} bytes, contains print + hello world"


def _check_result_txt(workspace: Path) -> tuple[bool, str]:
    """Mission 2: result.txt with at least 1 non-empty line."""
    f = workspace / "result.txt"
    if not f.exists():
        return False, f"missing {f}"
    lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return False, "result.txt empty"
    return True, f"result.txt {len(lines)} non-empty lines"


async def main() -> int:
    print("=" * 60)
    print("E2E verification of xai/sonnet fix (Phase 3)")
    print("=" * 60)

    # NOTE: Goal text used `print('hello world')` with literal newlines in
    # the prompt. On Windows the openclaw.cmd shim re-tokenises argv via
    # cmd.exe, which (a) treats apostrophes as metachars and (b) chops the
    # --message argument on embedded newlines. Either trap silently
    # collapses --model so the CLI falls back to its first listed
    # provider (openai/gpt-5.5) and aborts with auth-failed — exact same
    # pattern as the GeminiWorker cmd-wrapper trap fixed 2026-05-13.
    # Single-line prompt with double quotes round-trips cleanly. The
    # actual file content check (must contain "hello world" + "print")
    # is unchanged.
    r1 = await _run_one_mission(
        "hello",
        'Create a file named hello.py with this exact content (no extra text): print("hello world"). Then exit. Do not write any other file.',
        _check_hello_py,
    )
    print(json.dumps(r1, indent=2))

    r2 = await _run_one_mission(
        "result",
        "Create a file named result.txt in the current directory with two lines: line one says 'A', line two says 'B'. Then exit.",
        _check_result_txt,
    )
    print(json.dumps(r2, indent=2))

    print("\nSUMMARY")
    print("-" * 60)
    all_ok = True
    for r in (r1, r2):
        verdict = "PASS" if (
            r.get("stderr_clean")
            and r.get("stream_has_init_and_result")
            and r.get("artefact_ok")
            and r.get("duration_s", 9999) < 180
        ) else "FAIL"
        if verdict == "FAIL":
            all_ok = False
        print(f"  [{r['label']:7s}] {verdict}  "
              f"duration={r.get('duration_s','?')}s  "
              f"stderr_clean={r.get('stderr_clean')}  "
              f"artefact={r.get('artefact_ok')} ({r.get('artefact_detail','')})")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
