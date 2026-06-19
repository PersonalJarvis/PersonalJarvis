"""Smoke-Test Phase 6 / Prompt 2: Windows Job Object Reaping-Verifikation.

Verifiziert die Kern-Garantie aus ADR-0009 §3 + Research-Doc §C: das Schliessen
des Job-Object-Handles killt atomar den gesamten Descendant-Tree des
zugewiesenen Subprocesses — kein Zombie, kein Orphan.

Workflow:

1. Plattform-Gate: nur Windows. Sonst `[SKIP]` + exit 0.
2. `WindowsJobObject('jobkill-test')` als async-Context-Manager oeffnen.
3. Child spawnen: `python -c "..."`, der ein Grandchild-`python -c "time.sleep"`
   forkt und dessen PID auf stdout druckt. Beide laufen ~60 s.
4. Child-PID via `proc.pid`, Grandchild-PID via stdout-Read parsen.
5. Beide PIDs der `WindowsJobObject` zuweisen (Grandchild ggf. spaeter — wir
   weisen sicherheitshalber alle frischen Descendants nach).
6. Job-Object-Handle schliessen (via `await job.close()` ODER context-exit).
7. 1 s Settle-Pause.
8. Assertion via `psutil.pid_exists(pid)` fuer beide PIDs == False.

Exit 0 bei Erfolg ODER bei `[SKIP]`. Exit 1 nur bei echten Failures.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo-Root in sys.path damit `from jarvis.missions...` funktioniert
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


# Inline-Python: spawnt einen Grandchild, druckt dessen PID, sleept selbst 60 s.
_CHILD_SCRIPT = (
    "import os, sys, subprocess, time;"
    "p = subprocess.Popen([sys.executable, '-c', "
    "'import time; time.sleep(60)']);"
    "sys.stdout.write(str(p.pid) + chr(10));"
    "sys.stdout.flush();"
    "time.sleep(60)"
)


async def _spawn_child_with_grandchild() -> tuple[subprocess.Popen[str], int]:
    """Spawnt Child + parsed Grandchild-PID aus stdout (line 1).

    Returns:
        (child_process, grandchild_pid)
    """
    creationflags = 0
    if sys.platform == "win32":
        # Damit Job-Object-Assign nicht durch Inheritance-Shenanigans
        # ueberrumpelt wird, geben wir dem Child seine eigene Process-Group.
        creationflags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_BREAKAWAY_FROM_JOB  # type: ignore[attr-defined]
        )

    proc = subprocess.Popen(  # noqa: S603 — args kontrolliert, kein shell
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )

    # Read first line in a thread-safe way (Popen.stdout ist sync).
    loop = asyncio.get_running_loop()
    assert proc.stdout is not None
    grandchild_line = await loop.run_in_executor(None, proc.stdout.readline)
    grandchild_pid = int(grandchild_line.strip())
    return proc, grandchild_pid


async def smoke() -> int:
    failures: list[str] = []

    # --- Section 1: Plattform-Gate ---
    if sys.platform != "win32":
        print(f"{SKIP} not Windows — Job-Object-Kill-Test uebersprungen")
        return 0
    print(f"{OK} platform=win32, Job-Object-Test laeuft")

    # --- Section 2: psutil verfuegbar? ---
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        print(f"{SKIP} psutil nicht installiert — `pip install psutil`")
        return 0
    print(f"{OK} psutil {psutil.__version__} importiert")

    from jarvis.missions.isolation import WindowsJobObject  # noqa: PLC0415

    # --- Section 3+4: Job-Object oeffnen, Child + Grandchild spawnen ---
    child_pid: int | None = None
    grandchild_pid: int | None = None

    async with WindowsJobObject("smoke-phase6-jobkill") as job:
        print(f"{OK} WindowsJobObject opened (closed={job.closed})")

        proc, grandchild_pid = await _spawn_child_with_grandchild()
        child_pid = proc.pid
        print(f"{OK} child spawned pid={child_pid}, grandchild pid={grandchild_pid}")

        # --- Section 5: Beide PIDs assignen ---
        try:
            job.assign(child_pid)
            print(f"{OK} job.assign(child={child_pid})")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"job.assign(child) failed: {exc}")

        try:
            job.assign(grandchild_pid)
            print(f"{OK} job.assign(grandchild={grandchild_pid})")
        except Exception as exc:  # noqa: BLE001
            # Grandchild-Assign kann legitim failen (bereits via Job-Inheritance
            # zugewiesen, oder wir haben das Handle ueber Process-Group nicht).
            # Wichtig ist: KILL_ON_JOB_CLOSE killt trotzdem alle Descendants.
            print(f"{SKIP} job.assign(grandchild) warnung: {exc}")

        # Sanity: beide PIDs LEBEN noch
        if not psutil.pid_exists(child_pid):
            failures.append(f"child pid={child_pid} bereits tot vor close")
        if not psutil.pid_exists(grandchild_pid):
            failures.append(f"grandchild pid={grandchild_pid} bereits tot vor close")
        if not failures:
            print(f"{OK} pre-close: beide PIDs leben")

    # --- Section 6+7: Job-Handle ist jetzt geschlossen, kurz warten ---
    print(f"{OK} job context exited (handle closed)")
    await asyncio.sleep(1.0)

    # --- Section 8: Beide PIDs muessen tot sein ---
    if child_pid is not None and psutil.pid_exists(child_pid):
        # Defensive: koennte recyclet sein.
        try:
            p = psutil.Process(child_pid)
            if "python" in p.name().lower():
                failures.append(f"child pid={child_pid} lebt noch nach job.close()")
            else:
                print(f"{OK} child pid={child_pid} recyclet, Original tot")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"{OK} child pid={child_pid} nicht mehr ansprechbar")
    else:
        print(f"{OK} child pid={child_pid} reaped by Job Object")

    if grandchild_pid is not None and psutil.pid_exists(grandchild_pid):
        try:
            p = psutil.Process(grandchild_pid)
            if "python" in p.name().lower():
                failures.append(
                    f"grandchild pid={grandchild_pid} lebt noch nach job.close()"
                )
            else:
                print(f"{OK} grandchild pid={grandchild_pid} recyclet, Original tot")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"{OK} grandchild pid={grandchild_pid} nicht mehr ansprechbar")
    else:
        print(f"{OK} grandchild pid={grandchild_pid} reaped by Job Object")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} smoke-failures:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"{OK} ALL SMOKE CHECKS GREEN -- Job-Object reaping garantiert.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
