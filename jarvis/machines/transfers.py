"""Staged workspace handoff. Ownership changes only after destination hash verification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

from .service import machine_service

CHUNK_BYTES = 2 * 1024 * 1024


def file_manifest(root: Path, only: list[str] | None = None) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError("Source workspace does not exist")
    rows = []
    if only is None:
        candidates = sorted(root.rglob("*"))
    else:
        candidates = []
        for raw in only:
            selected_path = PurePosixPath(raw.replace("\\", "/"))
            if selected_path.is_absolute() or ".." in selected_path.parts or ":" in raw:
                raise PermissionError("Select relative paths inside the workspace")
            path = root.joinpath(*selected_path.parts)
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise PermissionError("Selected file is missing or outside the workspace")
            candidates.append(path)
    for path in candidates:
        relative = path.relative_to(root)
        if any(part.startswith(".jarvis-transfer-") for part in relative.parts):
            continue
        if path.is_symlink():
            raise PermissionError("Workspace transfer cannot include symbolic links")
        if not path.is_file():
            continue
        if (
            path.name == ".git"
            or path.name.startswith(".env")
            or path.suffix in {".pem", ".key", ".p12"}
        ):
            raise PermissionError(
                f"Remove machine-specific metadata or credentials before transfer: {relative}"
            )
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        rows.append({"path": relative.as_posix(), "size": path.stat().st_size, "sha256": sha})
    return rows


def write_transfer_chunk(path: Path, args: dict[str, Any]) -> dict[str, Any]:
    """Chunks only target newly staged files; repeated offsets are refused."""
    if not any(part.startswith(".jarvis-transfer-") for part in path.parts):
        raise PermissionError("Binary transfers require a staging directory")
    offset = int(args.get("offset", 0))
    if offset < 0:
        raise ValueError("Negative chunk offset")
    data = base64.b64decode(str(args.get("data", "")), validate=True)
    if len(data) > CHUNK_BYTES:
        raise ValueError("Transfer chunk exceeds its limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "xb" if offset == 0 else "r+b"
    with path.open(mode) as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() != offset:
            raise ValueError("Transfer offset mismatch; do not replay a chunk")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if args.get("final"):
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != args.get("sha256"):
            raise ValueError("Transferred file checksum mismatch")
        return {"bytes": path.stat().st_size, "sha256": checksum}
    return {"bytes": offset + len(data)}


class AgentTransfer:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.hub = machine_service(runtime.data_dir)

    async def move(
        self, agent_id: str, target: str, *, transfer_id: str | None = None
    ) -> dict[str, Any]:
        """Drain the canonical session, stage bytes, compare hashes, then change its host."""
        from jarvis.agent_chat.runner_api import supports_api_runner
        from jarvis.society.chat_binding import _workspace, pair_for

        await self.hub.start()
        agent = await self.runtime.roster.get(agent_id)
        if agent is None:
            raise ValueError("Agent does not exist")
        if not supports_api_runner(pair_for(self.runtime.config(), agent)[0]):
            raise PermissionError("This connector requires an API-capable agent runner")
        machine = await self.hub.store.machine(target) if target != "local" else None
        if machine is not None:
            if (
                not machine["capabilities"].get("agent_runtime")
                or target not in self.hub.connections
            ):
                raise PermissionError("Target needs an online connector with agent hosting")
            grant = await self.hub.store.grant(agent_id, target)
            if not grant.files:
                raise PermissionError("Target requires file-transfer permission")
        else:
            grant = None
        transfer_id = transfer_id or uuid4().hex
        old = await self.hub.store.reserve_transfer(agent_id, target, transfer_id)
        source = old["host_id"]
        original_workspace = agent.workspace_dir
        local_workspace_changed = False
        committed = False
        try:
            if source == target:
                raise ValueError("Agent is already on this host")
            chat = self.runtime.chat_service()
            deadline = time.monotonic() + 900
            while chat is not None and chat.is_running(agent.session_id):
                if time.monotonic() > deadline:
                    raise TimeoutError("Agent is still busy; stop its task before moving")
                await asyncio.sleep(0.25)
            source_root = (
                Path(_workspace(self.runtime.config(), agent)) if source == "local" else None
            )
            if source_root is not None:
                manifest = await asyncio.to_thread(file_manifest, source_root)
            else:
                source_grant = await self.hub.store.grant(agent_id, source)
                result = await self.call(
                    agent_id, source, "manifest", {"path": source_grant.workspace}, transfer_id
                )
                manifest = result["files"]
            stage_name = ".jarvis-transfer-" + transfer_id
            if target == "local":
                stage: Any = self.runtime.data_dir / "society" / agent_id / stage_name
                stage.mkdir(parents=True, exist_ok=False)
            else:
                assert machine is not None and grant is not None
                cls = (
                    PureWindowsPath if machine["capabilities"]["os"] == "windows" else PurePosixPath
                )
                stage = cls(grant.workspace) / stage_name
            await self.hub.store.write(
                "UPDATE transfers SET manifest=? WHERE id=?",
                (json.dumps({"files": manifest, "workspace": str(stage)}), transfer_id),
            )
            if not manifest and target != "local":
                await self.call(
                    agent_id,
                    target,
                    "write",
                    {
                        "path": str(stage / ".jarvis-workspace"),
                        "encoding": "base64",
                        "data": "",
                        "offset": 0,
                        "final": True,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                    },
                    transfer_id,
                )
            for entry in manifest:
                relative = PurePosixPath(entry["path"])
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in entry["path"]
                    or ":" in entry["path"]
                ):
                    raise PermissionError("Unsafe path in source manifest")
                dest = stage.joinpath(*relative.parts)
                size = int(entry["size"])
                for offset in range(0, max(1, size), CHUNK_BYTES):
                    if source_root is not None:
                        file_path = source_root.joinpath(*relative.parts)

                        def read_chunk(file=file_path, at=offset) -> bytes:
                            with file.open("rb") as stream:
                                stream.seek(at)
                                return stream.read(CHUNK_BYTES)

                        data = base64.b64encode(await asyncio.to_thread(read_chunk)).decode()
                    else:
                        source_path = (
                            str(PureWindowsPath(source_grant.workspace).joinpath(*relative.parts))
                            if (await self.hub.store.machine(source))["capabilities"]["os"]
                            == "windows"
                            else str(
                                PurePosixPath(source_grant.workspace).joinpath(*relative.parts)
                            )
                        )
                        result = await self.call(
                            agent_id,
                            source,
                            "read",
                            {"path": source_path, "offset": offset, "encoding": "base64"},
                            transfer_id,
                        )
                        data = result["data"]
                    args = {
                        "path": str(dest),
                        "data": data,
                        "encoding": "base64",
                        "offset": offset,
                        "final": offset + CHUNK_BYTES >= size,
                        "sha256": entry["sha256"],
                    }
                    if target == "local":
                        await asyncio.to_thread(write_transfer_chunk, dest, args)
                    else:
                        await self.call(agent_id, target, "write", args, transfer_id)
            # Destination activation is a single hub transaction. No remote scheduler exists.
            if target == "local":
                # The placement remains moving while the separate roster store is updated.
                await self.runtime.roster.update(agent_id, {"workspace_dir": str(stage)})
                local_workspace_changed = True
            db = await self.hub.store.connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if target != "local":
                    cursor = await db.execute("SELECT revoked FROM machines WHERE id=?", (target,))
                    current_machine = await cursor.fetchone()
                    if (
                        current_machine is None
                        or current_machine[0]
                        or target not in self.hub.connections
                    ):
                        raise PermissionError(
                            "Destination disconnected or was revoked during transfer"
                        )
                cursor = await db.execute(
                    "UPDATE placements SET host_id=?,generation=generation+1,moving=0 "
                    "WHERE agent_id=? AND moving=1 AND generation=?",
                    (target, agent_id, old["generation"]),
                )
                if cursor.rowcount != 1:
                    raise PermissionError("Agent ownership changed during transfer")
                if grant is not None:
                    moved_grant = grant.model_copy(update={"workspace": str(stage)})
                    await db.execute(
                        "UPDATE grants SET body=? WHERE agent_id=? AND machine_id=?",
                        (moved_grant.model_dump_json(), agent_id, target),
                    )
                await db.execute(
                    "UPDATE transfers SET state='complete',manifest=? WHERE id=?",
                    (json.dumps({"files": manifest, "workspace": str(stage)}), transfer_id),
                )
                await db.commit()
                committed = True
            finally:
                await db.close()
            return {
                "id": transfer_id,
                "state": "complete",
                "host_id": target,
                "workspace": str(stage),
            }
        except BaseException:
            if local_workspace_changed and not committed:
                await self.runtime.roster.update(agent_id, {"workspace_dir": original_workspace})
            await self.hub.store.write(
                "UPDATE transfers SET state='failed' WHERE id=?", (transfer_id,)
            )
            await self.hub.store.write(
                "UPDATE placements SET moving=0 WHERE agent_id=? AND generation=?",
                (agent_id, old["generation"]),
            )
            raise

    async def call(
        self, agent: str, host: str, operation: str, args: dict[str, Any], trace: str
    ) -> Any:
        result = await self.hub.execute_on_machine(
            agent_id=agent, machine_id=host, operation=operation, args=args, trace_id=trace
        )
        if not result["success"]:
            raise RuntimeError(result.get("error") or "Transfer failed")
        return result["output"]
