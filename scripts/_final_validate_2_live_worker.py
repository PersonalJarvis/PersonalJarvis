"""Final-Validation 2/5: prove ClaudeDirectWorker writes a real file via the
full Mission-Manager pipeline (bootstrap_missions -> manager.dispatch ->
kontrollierer.run_mission -> ClaudeDirectWorker subprocess).

Runs ENTIRELY in-process (no FastAPI). Goes through the same worker factory,
env builder, persona materialisation, Sonnet subprocess spawn the live system
uses. Refreshes the keyring with the current OAuth token from
~/.claude/.credentials.json before dispatch.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path(r"C:\Users\Administrator\Desktop\Personal Jarvis")
sys.path.insert(0, str(REPO))

import keyring  # noqa: E402

# ---------- 1. Refresh keyring + env from .credentials.json ----------
CREDS = Path.home() / ".claude" / ".credentials.json"
TOKEN = json.loads(CREDS.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
keyring.set_password("personal-jarvis", "anthropic_api_key", TOKEN)
os.environ["ANTHROPIC_OAUTH_TOKEN"] = TOKEN
os.environ["ANTHROPIC_API_KEY"] = TOKEN

# ---------- 2. Mission stack ----------
from jarvis.missions.init import bootstrap_missions, shutdown_missions  # noqa: E402
from jarvis.missions.state_machine import MissionState  # noqa: E402

LOG = REPO / "logs" / "final_validate_2.json"
LOG.parent.mkdir(parents=True, exist_ok=True)

REPORT: dict[str, object] = {
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "creds_path": str(CREDS),
    "token_prefix": TOKEN[:12] + "...",
    "transitions": [],
    "events": [],
}


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    REPORT.setdefault("trace", []).append(line)  # type: ignore[union-attr]


async def main() -> int:
    db = REPO / "data" / "missions.db"
    iso = REPO / "sub-agents-outputs"

    _log(f"bootstrap_missions: db={db.name} iso={iso.name}")
    stack = await bootstrap_missions(
        db_path=db,
        isolation_root=iso,
        repo_root=REPO,
        tts_speak_fn=None,
        brain_caller=None,
        per_mission_usd=2.0,
        daily_usd=20.0,
        safety_enabled=True,
        cleanup_startup_sweep=False,
        cleanup_daily=False,
    )
    manager = stack["manager"]
    kontrollierer = stack["kontrollierer"]
    _log("bootstrap complete")

    # Subscribe to ALL bus events for diagnostics.
    captured: list[dict[str, object]] = []

    def _sink(env: object) -> None:
        try:
            payload = getattr(env, "payload", None)
            kind = type(payload).__name__ if payload is not None else "?"
            captured.append(
                {
                    "kind": kind,
                    "mid": getattr(env, "mission_id", None),
                    "ts": getattr(env, "ts_ms", None),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    # subscribe_all on the mission bus
    try:
        manager.bus.subscribe_all(_sink)
    except Exception as exc:  # noqa: BLE001
        _log(f"subscribe_all failed: {exc}")

    marker = uuid.uuid4().hex[:8]
    prompt = (
        f"Create a file named final_proof.md in the current working directory "
        f"with these exact two lines:\n"
        f"# final validation\nmarker: {marker}\n"
        f"Use the Write tool. Then print: Written: final_proof.md."
    )
    REPORT["marker"] = marker
    REPORT["prompt"] = prompt

    mid = await manager.dispatch(prompt=prompt, language="en")
    REPORT["mission_id"] = mid
    _log(f"dispatched mid={mid} marker={marker}")

    # Drive run_mission (this is what the live Voice path does on the
    # /api/missions REST/WS handler).
    t0 = time.time()
    final_state: MissionState | None = None
    try:
        final_state = await asyncio.wait_for(
            kontrollierer.run_mission(mid), timeout=240.0
        )
        _log(f"run_mission returned: {final_state}")
    except asyncio.TimeoutError:
        _log("run_mission TIMEOUT after 240s")
        REPORT["timeout"] = True
    except Exception as exc:  # noqa: BLE001
        _log(f"run_mission EXCEPTION: {exc!r}")
        REPORT["exception"] = repr(exc)

    REPORT["wall_s"] = round(time.time() - t0, 2)
    REPORT["final_state"] = str(final_state) if final_state else None
    REPORT["events"] = captured[-80:]  # keep last 80 events

    # ---------- 3. Forensics on mission_<short> ----------
    short = mid[:8]
    mdir = iso / f"mission_{short}"
    _log(f"forensic root: {mdir}")
    REPORT["mission_dir"] = str(mdir)

    tool_use_counts: dict[str, int] = {}
    write_tool_invocations: list[dict[str, object]] = []
    stream_files: list[dict[str, object]] = []

    for stream in mdir.rglob("stream.jsonl"):
        try:
            raw = stream.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _log(f"stream read fail {stream}: {exc}")
            continue
        n_lines = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "assistant":
                msg = rec.get("message", {})
                for blk in msg.get("content", []) or []:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        name = blk.get("name", "?")
                        tool_use_counts[name] = tool_use_counts.get(name, 0) + 1
                        if name in ("Write", "Edit", "Create"):
                            write_tool_invocations.append(
                                {
                                    "name": name,
                                    "stream": str(stream.relative_to(REPO)),
                                    "input_keys": list(
                                        (blk.get("input") or {}).keys()
                                    ),
                                    "file_path": (
                                        (blk.get("input") or {}).get(
                                            "file_path"
                                        )
                                    ),
                                    "content_snippet": (
                                        ((blk.get("input") or {})
                                         .get("content", "")[:160])
                                    ),
                                }
                            )
        stream_files.append(
            {
                "path": str(stream.relative_to(REPO)),
                "bytes": stream.stat().st_size,
                "lines": n_lines,
            }
        )

    REPORT["stream_files"] = stream_files
    REPORT["tool_use_counts"] = tool_use_counts
    REPORT["write_tool_invocations"] = write_tool_invocations

    # ---------- 4. Diff preservation ----------
    diff_artifacts: list[dict[str, object]] = []
    marker_found_in_diff = False
    for task_dir in (mdir / "tasks").glob("*"):
        for diff in task_dir.glob("**/diff.iter*.patch"):
            blob = diff.read_text(encoding="utf-8", errors="replace")
            diff_artifacts.append(
                {
                    "path": str(diff.relative_to(REPO)),
                    "bytes": diff.stat().st_size,
                    "marker_in_text": marker in blob,
                }
            )
            if marker in blob:
                marker_found_in_diff = True

    REPORT["diff_artifacts"] = diff_artifacts
    REPORT["marker_found_in_diff"] = marker_found_in_diff

    # ---------- 5. Worktree on-disk file check ----------
    worktree_files: list[dict[str, object]] = []
    for task_dir in (mdir / "tasks").glob("*"):
        wt = task_dir / "worktree"
        if not wt.is_dir():
            continue
        proof = wt / "final_proof.md"
        if proof.is_file():
            content = proof.read_text(encoding="utf-8", errors="replace")
            worktree_files.append(
                {
                    "path": str(proof.relative_to(REPO)),
                    "bytes": proof.stat().st_size,
                    "marker_in_file": marker in content,
                    "snippet": content[:200],
                }
            )

    REPORT["worktree_files"] = worktree_files
    REPORT["marker_found_in_worktree_file"] = any(
        wf.get("marker_in_file") for wf in worktree_files
    )

    # ---------- 6. Verdict ----------
    write_seen = tool_use_counts.get("Write", 0) >= 1
    diff_ok = bool(diff_artifacts)
    verdict = (
        "PASS"
        if (write_seen and (REPORT["marker_found_in_diff"]
                            or REPORT["marker_found_in_worktree_file"]))
        else ("PARTIAL" if write_seen else "FAIL")
    )
    REPORT["verdict"] = verdict
    _log(f"VERDICT={verdict} write_seen={write_seen} diff_ok={diff_ok}")

    LOG.write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
    _log(f"report -> {LOG}")

    await shutdown_missions(stack)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
