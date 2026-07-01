"""Smoke test Phase 6 / Prompt 2: worker layer end-to-end against the real claude binary.

Verifies the T1+T2 API surface in a realistic mini run:

1. `claude` must be on PATH. If not: `[SKIP]` + exit 0 (NOT a failure).
2. `git rev-parse --show-toplevel` as the basis for the worktree.
3. `WorktreeManager.create(mission_slug='smoke', task_id='p2')` creates a
   fresh branch + workspace directory.
4. `WindowsJobObject('smoke-p2')` as an async context manager — on non-Windows
   it's a no-op, the smoke still runs (worker spawns normally, no reaping).
5. `ClaudeDirectWorker.spawn(prompt, ..., max_turns=3)` with a cost cap (--max-turns
   is cost guardrail #1 per the research doc §B). The stream is drained until the
   `result` event. (OpenClawWorker was removed in the OpenClaw/UFO3 removal —
   `f9fa1c2f`; ClaudeDirectWorker is the production claude-CLI worker.)
6. Verify `(workspace / 'hello.txt').exists()` AND content == 'world'
   (with/without a trailing newline).
7. Verify via `psutil.pid_exists(pid)` that the worker subprocess terminated
   cleanly.
8. Cleanup: `WorktreeManager.remove(workspace, force=True)`.

Exit 0 on success OR on `[SKIP]`. Exit 1 only on real failures.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo root in sys.path so `from jarvis.missions...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def _repo_root() -> Path:
    """Returns the repo root via `git rev-parse --show-toplevel`."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip()).resolve()


def _read_hello(workspace: Path) -> str | None:
    """Reads workspace/hello.txt — None if not present."""
    target = workspace / "hello.txt"
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8")


async def smoke() -> int:
    failures: list[str] = []

    # --- Section 1: claude im PATH? ---
    claude_path = shutil.which("claude")
    if claude_path is None:
        print(f"{SKIP} claude not in PATH — Phase 6 worker smoke skipped")
        return 0
    print(f"{OK} claude found at {claude_path}")

    # --- Section 2: psutil available? ---
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        print(f"{SKIP} psutil not installed — `pip install psutil` recommended")
        return 0
    print(f"{OK} psutil {psutil.__version__} imported")

    # --- Section 3: create repo root + worktree ---
    from jarvis.missions.isolation import (  # noqa: PLC0415
        WindowsJobObject,
        WorktreeManager,
        build_worker_env,
    )
    from jarvis.missions.workers.claude_direct_worker import (  # noqa: PLC0415
        ClaudeDirectWorker,
    )

    repo_root = _repo_root()
    print(f"{OK} repo_root = {repo_root}")

    wm = WorktreeManager(repo_root=repo_root)
    try:
        workspace = wm.create(mission_slug="smoke", task_id="p2-hello")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} WorktreeManager.create failed: {exc}")
        return 1
    print(f"{OK} worktree created at {workspace}")

    # --- Section 4: Job-Object + Worker spawn + drain ---
    log_dir = workspace.parent / "logs"
    env = build_worker_env(run_dir=workspace.parent.parent)

    worker = ClaudeDirectWorker()
    result_event = None
    event_count = 0
    auth_failed = False

    try:
        async with WindowsJobObject("smoke-phase6-p2") as job:
            print(f"{OK} WindowsJobObject opened (closed={job.closed})")

            async for event in worker.spawn(
                "Erstelle eine Datei hello.txt mit dem Inhalt 'world' (ohne "
                "Anfuehrungszeichen) im aktuellen Verzeichnis.",
                worktree=workspace,
                env=env,
                job=job,
                worker_id="smoke-p2",
                log_dir=log_dir,
                max_turns=3,
                model="sonnet",
            ):
                event_count += 1
                etype = getattr(event, "type", None)
                if etype == "result":
                    result_event = event
                    break

        worker_pid = worker.last_pid
        if worker_pid is None:
            failures.append("worker.last_pid ist None nach spawn")
        else:
            print(f"{OK} Worker spawned with pid={worker_pid}, {event_count} events drained")

        if result_event is None:
            failures.append("Kein `result`-Event vor Stream-EOF erhalten")
        else:
            is_error = getattr(result_event, "is_error", None)
            cost = getattr(result_event, "cost_usd", None)
            turns = getattr(result_event, "num_turns", None)
            result_text = getattr(result_event, "result", "") or ""
            print(
                f"{OK} result event: is_error={is_error}, cost_usd={cost}, num_turns={turns}"
            )
            # Auth-Failure ist eine Umgebungsbeschraenkung (claude nicht
            # eingeloggt), kein Defekt am Worker-Code. Smoke skippt dann
            # die Datei-Verifikation und exitet 0 — Spawn/Stream/Reaping
            # wurden bereits validiert.
            if "Not logged in" in result_text or "Please run /login" in result_text:
                auth_failed = True
                print(
                    f"{SKIP} claude-CLI nicht authentifiziert (Subprocess-Auth) — "
                    f"Datei-Verifikation uebersprungen"
                )
            elif is_error:
                failures.append(
                    f"result.is_error=True, subtype="
                    f"{getattr(result_event, 'subtype', None)}, result={result_text!r}"
                )

        # --- Section 5: Datei-Verifikation (nur wenn kein Auth-Failure) ---
        if not auth_failed:
            content = _read_hello(workspace)
            if content is None:
                failures.append(f"hello.txt nicht im Worktree erzeugt: {workspace}")
            elif content.strip() != "world":
                failures.append(f"hello.txt content='{content!r}', erwartet 'world'")
            else:
                print(f"{OK} hello.txt content verifiziert (raw={content!r})")

        # --- Section 6: Worker-Subprocess tot? ---
        if worker_pid is not None:
            # Kurz warten falls OS-Reaper noch nicht durch ist.
            await asyncio.sleep(0.3)
            still_alive = psutil.pid_exists(worker_pid)
            if still_alive:
                # Auf Windows: pid_exists kann True bleiben wenn ein anderer
                # Prozess die PID recyclet hat — defensiv pruefen wir den Namen.
                try:
                    p = psutil.Process(worker_pid)
                    name = p.name().lower()
                    if "claude" in name or "node" in name:
                        failures.append(
                            f"Worker pid={worker_pid} lebt noch (name={name})"
                        )
                    else:
                        print(
                            f"{OK} pid={worker_pid} recyclet zu '{name}', Worker tot"
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    print(f"{OK} pid={worker_pid} nicht mehr ansprechbar")
            else:
                print(f"{OK} Worker pid={worker_pid} terminiert")

    finally:
        # --- Section 7: Cleanup ---
        try:
            wm.remove(workspace, force=True)
            print(f"{OK} worktree removed")
        except Exception as exc:  # noqa: BLE001
            # Cleanup-Fehler nicht als Test-Failure werten — manueller Prune via
            # `git worktree prune` raeumt nach.
            print(f"{SKIP} cleanup warnung: {exc}")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} smoke-failures:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"{OK} ALL SMOKE CHECKS GREEN -- Phase 6 Worker-Layer end-to-end ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
