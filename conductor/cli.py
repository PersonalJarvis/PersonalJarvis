"""Conductor-CLI.

Subcommands:
- ``list``          — alle Jobs
- ``show <id>``     — Detail inkl. letzte Runs
- ``add <yaml>``    — Job aus YAML-File einlesen und upsert-en
- ``run <id>``      — Manual trigger; wartet auf Terminal-State
- ``runs``          — Run-Timeline (letzte 30)
- ``toggle <id>``   — enabled flippen
- ``delete <id>``   — Job entfernen
- ``serve [--port]`` — Standalone FastAPI auf Port 7777

DB-Pfad: ``~/.conductor/conductor.sqlite`` (oder ``CONDUCTOR_DB_PATH``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Windows-UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass


def _resolve_db_path() -> Path | None:
    env = os.environ.get("CONDUCTOR_DB_PATH")
    return Path(env) if env else None


async def _with_store(fn):
    from .core.store import ConductorStore
    store = ConductorStore(_resolve_db_path())
    await store.init()
    try:
        return await fn(store)
    finally:
        await store.close()


# ----------------------------------------------------------------------
# Subcommand-Handler
# ----------------------------------------------------------------------

async def cmd_list(_: argparse.Namespace) -> int:
    async def _run(store):
        rows = await store.list_jobs()
        if not rows:
            print("Keine Jobs. 'python -m conductor add <yaml>' legt welche an.")
            return 0
        _print_jobs_table(rows)
        return 0
    return await _with_store(_run)


async def cmd_show(args: argparse.Namespace) -> int:
    async def _run(store):
        row = await store.get_job(args.id)
        if row is None:
            print(f"Job {args.id} nicht gefunden", file=sys.stderr)
            return 1
        print(json.dumps({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "enabled": bool(row["enabled"]),
            "type": row["type"],
            "schedule": row["schedule_type"],
            "schedule_expr": row.get("schedule_expr"),
            "spec": json.loads(row["spec_json"]),
            "last_run_state": row.get("last_run_state"),
            "last_run_at_ns": row.get("last_run_at_ns"),
            "next_run_at_ns": row.get("next_run_at_ns"),
        }, indent=2, ensure_ascii=False))
        runs = await store.list_runs(job_id=args.id, limit=5)
        if runs:
            print("\nRecent runs:")
            for r in runs:
                print(f"  [{r['state']:9}] {r['id'][:8]}  "
                      f"exit={r['exit_code']}  "
                      f"trigger={r['trigger']}")
        return 0
    return await _with_store(_run)


async def cmd_add(args: argparse.Namespace) -> int:
    path = Path(args.yaml_file)
    if not path.exists():
        print(f"Datei nicht gefunden: {path}", file=sys.stderr)
        return 1

    from .core.seed import load_job_from_yaml

    async def _run(store):
        try:
            job = await load_job_from_yaml(path)
        except Exception as exc:  # noqa: BLE001
            print(f"YAML-Parse/Validate fehlgeschlagen: {exc}",
                  file=sys.stderr)
            return 1
        jid = await store.upsert_job(job)
        print(f"Job angelegt/aktualisiert: {jid}  ({job.name})")
        return 0
    return await _with_store(_run)


async def cmd_run(args: argparse.Namespace) -> int:
    from .core.runner import Runner

    async def _run(store):
        job = await store.get_job(args.id)
        if job is None:
            print(f"Job {args.id} nicht gefunden", file=sys.stderr)
            return 1

        # Simple-Callback: loggt Lifecycle auf stderr
        def _cb(event: str, payload: dict) -> None:
            print(f"[{event}] {json.dumps(payload, ensure_ascii=False)[:200]}",
                  file=sys.stderr)

        runner = Runner(store, on_event=_cb)
        input_data: dict[str, Any] = {}
        if args.input_json:
            try:
                input_data = json.loads(args.input_json)
            except json.JSONDecodeError as exc:
                print(f"--input-json parse error: {exc}", file=sys.stderr)
                return 1
        run_id = await runner.trigger(args.id, trigger="manual",
                                       input_data=input_data)
        print(f"Run gestartet: {run_id}", file=sys.stderr)

        # Poll bis Terminal-State
        for _ in range(args.timeout * 2):
            await asyncio.sleep(0.5)
            run = await store.get_run(run_id)
            if run and run["state"] in ("completed", "failed", "cancelled"):
                break
        else:
            print("Timeout beim Warten auf Terminal-State", file=sys.stderr)
            return 2

        run = await store.get_run(run_id)
        if run is None:
            return 2
        if run["state"] == "completed":
            print(run["output"])
            return 0
        print(f"Run fehlgeschlagen: {run.get('error') or 'unknown'}",
              file=sys.stderr)
        if run["output"]:
            print(run["output"])
        return 1
    return await _with_store(_run)


async def cmd_runs(args: argparse.Namespace) -> int:
    async def _run(store):
        runs = await store.list_runs(limit=args.limit)
        if not runs:
            print("Noch keine Runs.")
            return 0
        print(f"{'STATE':<10} {'TRIGGER':<9} {'STARTED':<20} "
              f"{'EXIT':<5} {'JOB':<36}")
        print("-" * 85)
        for r in runs:
            try:
                from datetime import datetime
                started = datetime.fromtimestamp(
                    r["started_at_ns"] / 1e9).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:  # noqa: BLE001
                started = "—"
            exit_c = r["exit_code"]
            exit_s = "" if exit_c is None else str(exit_c)
            print(f"{r['state']:<10} {r['trigger']:<9} {started:<20} "
                  f"{exit_s:<5} {r['job_id']:<36}")
        return 0
    return await _with_store(_run)


async def cmd_toggle(args: argparse.Namespace) -> int:
    async def _run(store):
        row = await store.get_job(args.id)
        if row is None:
            print(f"Job {args.id} nicht gefunden", file=sys.stderr)
            return 1
        new_enabled = not bool(row["enabled"])
        await store.set_enabled(args.id, new_enabled)
        if not new_enabled:
            await store.set_next_run(args.id, None)
        print(f"Job {row['name']}: enabled={new_enabled}")
        return 0
    return await _with_store(_run)


async def cmd_delete(args: argparse.Namespace) -> int:
    async def _run(store):
        ok = await store.delete_job(args.id)
        if not ok:
            print(f"Job {args.id} nicht gefunden", file=sys.stderr)
            return 1
        print(f"Job {args.id} entfernt")
        return 0
    return await _with_store(_run)


async def cmd_seed(args: argparse.Namespace) -> int:
    """Pflanzt die Seed-YAMLs aus conductor/seed/. --force uebrschreibt."""
    from .core.seed import ensure_seed_jobs

    async def _run(store):
        added = await ensure_seed_jobs(store, force=args.force)
        verb = "ueberschrieben" if args.force else "neu angelegt"
        print(f"{added} Seed-Jobs {verb}.")
        return 0
    return await _with_store(_run)


def cmd_serve(args: argparse.Namespace) -> int:
    """Standalone FastAPI-Server. Nicht async — uvicorn.run ist blocking."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn nicht installiert — pip install uvicorn[standard]",
              file=sys.stderr)
        return 1
    from .api.app import create_app
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port,
                 log_level="info")
    return 0


# ----------------------------------------------------------------------
# Pretty-Print
# ----------------------------------------------------------------------

def _print_jobs_table(rows: list[dict]) -> None:
    header = f"{'ID':<36}  {'NAME':<25}  {'TYPE':<6}  {'SCHEDULE':<25}  {'ENABLED'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        sched_str = r.get("schedule_expr") or r.get("schedule_type") or ""
        if r.get("schedule_type") == "interval" and sched_str:
            sched_str = f"every {sched_str}s"
        elif r.get("schedule_type") == "cron":
            sched_str = f"cron: {sched_str}"
        enabled = "yes" if r.get("enabled") else "no"
        print(f"{r['id']}  {r['name'][:25]:<25}  {r['type']:<6}  "
              f"{sched_str[:25]:<25}  {enabled}")


# ----------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="conductor",
        description="Conductor — schedule tasks + agentic workflows (OSS).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Alle Jobs auflisten")

    show = sub.add_parser("show", help="Job-Detail")
    show.add_argument("id")

    add = sub.add_parser("add", help="Job aus YAML anlegen/aktualisieren")
    add.add_argument("yaml_file", help="Pfad zur YAML-Datei")

    run_p = sub.add_parser("run", help="Job manuell triggern + auf Ende warten")
    run_p.add_argument("id")
    run_p.add_argument("--input-json", default=None,
                        help="Optional: JSON-String als Input fuer den Run.")
    run_p.add_argument("--timeout", type=int, default=120,
                        help="Max Wartezeit in Sekunden (Default 120).")

    runs = sub.add_parser("runs", help="Run-Timeline")
    runs.add_argument("--limit", type=int, default=30)

    tog = sub.add_parser("toggle", help="enabled flippen")
    tog.add_argument("id")

    dele = sub.add_parser("delete", help="Job entfernen")
    dele.add_argument("id")

    seed = sub.add_parser("seed",
                           help="Seed-YAMLs pflanzen (--force ueberschreibt)")
    seed.add_argument("--force", action="store_true",
                      help="Bestehende Jobs mit gleicher ID ueberschreiben.")

    serve = sub.add_parser("serve", help="Standalone FastAPI auf 7777")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7777)

    args = p.parse_args(argv)

    if args.cmd == "serve":
        return cmd_serve(args)

    handlers = {
        "list":   cmd_list,
        "show":   cmd_show,
        "add":    cmd_add,
        "run":    cmd_run,
        "runs":   cmd_runs,
        "toggle": cmd_toggle,
        "delete": cmd_delete,
        "seed":   cmd_seed,
    }
    return asyncio.run(handlers[args.cmd](args))


if __name__ == "__main__":
    sys.exit(main())
