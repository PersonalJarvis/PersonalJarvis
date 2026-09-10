"""Connector-side execution with a monotonic lease and durable duplicate suppression."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .models import MachineCommand, MachineResult

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 2 * 1024 * 1024


class ConnectorRunner:
    def __init__(self, root: Path, *, desktop_mode: str = "none") -> None:
        from .desktop import ConnectorDesktops

        self.desktops = ConnectorDesktops(desktop_mode)
        self.desktops.execution_guard = self.check_lease
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.deadline = 0.0
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self.rpc: Any = None
        with sqlite3.connect(root / "journal.db") as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, body TEXT, result TEXT)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "delivered" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0")

    def renew(self, seconds: float) -> None:
        self.deadline = time.monotonic() + min(max(seconds, 0), 30)

    def check_lease(self) -> None:
        if time.monotonic() >= self.deadline:
            raise PermissionError("Execution lease expired")

    def _claim(self, command: MachineCommand) -> MachineResult | None:
        with sqlite3.connect(self.root / "journal.db") as db:
            row = db.execute(
                "SELECT body,result FROM jobs WHERE id=?", (command.job_id,)
            ).fetchone()
            if row is not None:
                if row[0] != command.model_dump_json():
                    raise PermissionError("Job identifier reused with different arguments")
                if row[1]:
                    return MachineResult.model_validate_json(row[1])
                return MachineResult(
                    job_id=command.job_id,
                    success=False,
                    error="Previous execution has an unknown outcome; do not replay",
                    uncertain=True,
                )
            db.execute(
                "INSERT INTO jobs(id,body,result) VALUES (?,?,NULL)",
                (command.job_id, command.model_dump_json()),
            )
        return None

    def _save(self, result: MachineResult) -> None:
        with sqlite3.connect(self.root / "journal.db") as db:
            db.execute(
                "UPDATE jobs SET result=? WHERE id=?", (result.model_dump_json(), result.job_id)
            )

    async def completed(self) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            with sqlite3.connect(self.root / "journal.db") as db:
                return [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT result FROM jobs WHERE result IS NOT NULL AND delivered=0 "
                        "ORDER BY rowid LIMIT 1"
                    )
                ]

        return await asyncio.to_thread(read)

    async def acknowledge(self, job_id: str) -> None:
        def update() -> None:
            with sqlite3.connect(self.root / "journal.db") as db:
                db.execute("UPDATE jobs SET delivered=1 WHERE id=?", (job_id,))

        await asyncio.to_thread(update)

    def path(self, command: MachineCommand) -> Path:
        root = Path(command.grant.workspace).expanduser().resolve()
        raw = str(command.args.get("path") or command.args.get("cwd") or ".")
        target = Path(raw).expanduser()
        target = (root / target).resolve() if not target.is_absolute() else target.resolve()
        if command.grant.scope == "workspace" and not target.is_relative_to(root):
            raise PermissionError("Path leaves the permitted workspace")
        return target

    async def run(self, command: MachineCommand) -> MachineResult:
        self.check_lease()
        async with self._lock:
            duplicate = await asyncio.to_thread(self._claim, command)
        if duplicate is not None:
            return duplicate
        try:
            if command.agent_id != command.grant.agent_id:
                raise PermissionError("Grant belongs to another agent")
            self.check_lease()
            output = await self._execute(command)
            result = MachineResult(job_id=command.job_id, success=True, output=output)
            if command.operation == "shell" and output["exit_code"] != 0:
                result.success = False
                result.error = f"Shell exited with {output['exit_code']}"
        except asyncio.CancelledError:
            result = MachineResult(
                job_id=command.job_id,
                success=False,
                error="Execution stopped; effects already performed are not rolled back",
            )
            await asyncio.to_thread(self._save, result)
            raise
        except Exception as exc:  # noqa: BLE001 — structured failure is returned to the hub
            log.info("Machine job %s failed: %s", command.job_id, exc)
            result = MachineResult(job_id=command.job_id, success=False, error=str(exc))
        await asyncio.to_thread(self._save, result)
        return result

    async def _execute(self, command: MachineCommand) -> Any:
        if command.operation == "agent_turn":
            from .host_loop import run_host_loop

            if self.rpc is None:
                raise PermissionError("Hub model gateway is unavailable")
            return await run_host_loop(command, self.rpc, self.check_lease)
        if command.operation == "shell":
            if not command.grant.shell or command.grant.scope != "account":
                raise PermissionError(
                    "This connector does not provide an isolated shell; account access is required"
                )
            return await self._shell(command)
        if command.operation == "desktop":
            return await self.desktops.execute(command)
        if not command.grant.files:
            raise PermissionError("File access is not granted")
        self.check_lease()
        return await asyncio.to_thread(self._file, command)

    def _file(self, command: MachineCommand) -> Any:
        self.check_lease()
        path = self.path(command)
        if command.operation == "manifest":
            from .transfers import file_manifest

            return {"files": file_manifest(path, command.args.get("paths"))}
        if command.operation == "read":
            offset = int(command.args.get("offset", 0))
            if offset < 0:
                raise ValueError("Offset must not be negative")
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(MAX_FILE_BYTES + 1)
            binary = command.args.get("encoding") == "base64"
            if len(data) > MAX_FILE_BYTES and not binary:
                raise ValueError("File exceeds the inline transfer limit")
            if binary:
                return {
                    "data": base64.b64encode(data[:MAX_FILE_BYTES]).decode(),
                    "size": path.stat().st_size,
                }
            return {"path": str(path), "text": data.decode("utf-8")}
        if command.operation == "list":
            return {
                "path": str(path),
                "entries": [
                    {"name": p.name, "directory": p.is_dir()} for p in sorted(path.iterdir())[:1000]
                ],
            }
        if command.operation == "write":
            if command.args.get("encoding") == "base64":
                from .transfers import write_transfer_chunk

                return write_transfer_chunk(path, command.args)
            data = str(command.args.get("text", "")).encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("File exceeds the inline transfer limit")
            # Exclusive temporary file, same directory, then atomic replacement.
            import tempfile

            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".jarvis-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.check_lease()
                if self.path(command) != path:
                    raise PermissionError("Destination changed during transfer")
                os.replace(temp_name, path)
            finally:
                Path(temp_name).unlink(missing_ok=True)
            return {"path": str(path), "bytes": len(data)}
        raise ValueError("Unsupported operation")

    async def _shell(self, command: MachineCommand) -> dict[str, Any]:
        from jarvis.agent_chat.tools import shell_argv

        text = str(command.args.get("command", "")).strip()
        if not text:
            raise ValueError("Command is required")
        cwd = self.path(command)
        if not cwd.is_dir():
            raise ValueError("Remote working directory does not exist")
        env = dict(os.environ, CI="1", NO_COLOR="1", PYTHONIOENCODING="utf-8")
        proc = await asyncio.create_subprocess_exec(
            *shell_argv(),
            text,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            start_new_session=os.name != "nt",
        )
        output = bytearray()
        try:
            birth = _process_birth(proc.pid)
        except Exception:
            proc.kill()
            await proc.wait()
            raise

        async def drain() -> None:
            assert proc.stdout is not None
            while chunk := await proc.stdout.read(8192):
                output.extend(chunk)
                if len(output) > MAX_FILE_BYTES:
                    del output[: len(output) - MAX_FILE_BYTES]

        reader = asyncio.create_task(drain())
        started = time.monotonic()
        try:
            while proc.returncode is None:
                self.check_lease()
                if time.monotonic() - started > command.timeout_s:
                    raise TimeoutError("Command timed out")
                await asyncio.sleep(0.1)
            await asyncio.wait_for(reader, 2)
            return {
                "output": output.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode,
                "cwd": str(cwd),
            }
        finally:
            await asyncio.to_thread(_kill_tree, proc.pid, birth)
            with contextlib.suppress(TimeoutError):  # Child may have already exited.
                await asyncio.wait_for(proc.wait(), 3)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def _process_birth(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None  # Fast command already exited before identity capture.


def _kill_tree(pid: int, birth: float | None) -> None:
    import psutil

    try:
        process = psutil.Process(pid)
        if birth is None or process.create_time() != birth:
            return  # Never signal a recycled process id.
        children = process.children(recursive=True)
        for child in reversed(children):
            with contextlib.suppress(psutil.NoSuchProcess):  # Already exited during collection.
                child.kill()
        process.kill()
        psutil.wait_procs([process, *children], timeout=3)
    except psutil.NoSuchProcess:
        return  # The process already exited; there is nothing left to signal.
