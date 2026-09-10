"""Create a paused independent identity; never copy logins or the canonical chat."""

from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4


async def clone_agent(
    runtime: Any, agent_id: str, name: str, files: list[str] | None = None
) -> dict[str, Any]:
    from jarvis.mcp.agents.portable import PORTABLE_FIELDS
    from jarvis.society.roster import slugify
    from jarvis.society.routines import agent_tag, list_routines

    source = await runtime.roster.get(agent_id)
    if source is None:
        raise ValueError("Source agent does not exist")
    if str(source.tier) == "lead":
        raise PermissionError("A society has one lead; copy a specialist or orchestrator")
    if await runtime.roster.get(slugify(name)) is not None:
        raise ValueError("The copy needs a new, unique name")
    fields = {
        key: value
        for key, value in source.to_dict().items()
        if key in PORTABLE_FIELDS and key != "name"
    }
    clone, created = await runtime.roster.create(name=name, state="paused", **fields)
    if not created:
        raise ValueError("An agent with this name already exists")
    if files:
        await _copy_files(runtime, source, clone, files)
        clone = await runtime.roster.get(clone.agent_id)
    pairs = [
        (
            runtime.memory.namespace(runtime.memory.root(), source.agent_id),
            runtime.memory.namespace(runtime.memory.root(), clone.agent_id),
        ),
        (runtime.skills_for(source.agent_id).root, runtime.skills_for(clone.agent_id).root),
    ]
    for source_dir, destination in pairs:
        if source_dir.is_dir():
            await asyncio.to_thread(_copy_namespace, source_dir, destination)
    store, _scheduler = runtime.task_services()
    routine_ids = []
    if store is not None:
        for row in await list_routines(store, source.agent_id):
            spec = await store.get_spec(row["id"])
            if spec is None:
                continue
            changes: dict[str, Any] = {
                "id": uuid4(),
                "created_at_ns": 0,
                "title": spec.title.replace(f"[agent:{source.name}]", f"[agent:{clone.name}]", 1),
                "tags": tuple(
                    agent_tag(clone.agent_id) if tag == agent_tag(source.agent_id) else tag
                    for tag in spec.tags
                ),
            }
            if spec.action.kind == "agent":
                changes["action"] = spec.action.model_copy(
                    update={
                        "prompt": spec.action.prompt.replace(
                            f"You are {source.name}", f"You are {clone.name}", 1
                        ),
                        "plugin_grants": (),
                    }
                )
            tid = await store.insert(spec.model_copy(update=changes))
            await store.update_state(tid, "paused")
            routine_ids.append(tid)
    return {"agent": clone.to_dict(), "routine_ids": routine_ids, "state": "paused"}


def _copy_namespace(source: Path, destination: Path) -> None:
    from .transfers import file_manifest

    file_manifest(source)  # Refuse symlinks, external worktree metadata and credential files.
    shutil.copytree(source, destination, dirs_exist_ok=False)


async def _copy_files(runtime: Any, source: Any, clone: Any, paths: list[str]) -> None:
    from jarvis.society.chat_binding import _workspace

    from .transfers import CHUNK_BYTES, AgentTransfer, file_manifest, write_transfer_chunk

    transfer = AgentTransfer(runtime)
    await transfer.hub.start()
    rows = await transfer.hub.store.rows(
        "SELECT host_id FROM placements WHERE agent_id=?", (source.agent_id,)
    )
    host = rows[0]["host_id"] if rows else "local"
    trace = "copy:" + uuid4().hex
    if host == "local":
        root: Any = Path(_workspace(runtime.config(), source))
        manifest = await asyncio.to_thread(file_manifest, root, paths)
    else:
        grant = await transfer.hub.store.grant(source.agent_id, host)
        machine = await transfer.hub.store.machine(host)
        root = (
            PureWindowsPath(grant.workspace)
            if machine["capabilities"]["os"] == "windows"
            else PurePosixPath(grant.workspace)
        )
        manifest = (
            await transfer.call(
                source.agent_id, host, "manifest", {"path": str(root), "paths": paths}, trace
            )
        )["files"]
    stage = runtime.data_dir / "society" / clone.agent_id / (".jarvis-transfer-" + uuid4().hex)
    for entry in manifest:
        relative = PurePosixPath(entry["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in entry["path"]
            or ":" in entry["path"]
        ):
            raise PermissionError("Unsafe path in copy manifest")
        source_path = root.joinpath(*relative.parts)
        for offset in range(0, max(1, int(entry["size"])), CHUNK_BYTES):
            if host == "local":

                def read_chunk(file=source_path, at=offset):
                    with file.open("rb") as stream:
                        stream.seek(at)
                        return base64.b64encode(stream.read(CHUNK_BYTES)).decode()

                data = await asyncio.to_thread(read_chunk)
            else:
                data = (
                    await transfer.call(
                        source.agent_id,
                        host,
                        "read",
                        {"path": str(source_path), "encoding": "base64", "offset": offset},
                        trace,
                    )
                )["data"]
            await asyncio.to_thread(
                write_transfer_chunk,
                stage.joinpath(*relative.parts),
                {
                    "data": data,
                    "offset": offset,
                    "final": offset + CHUNK_BYTES >= entry["size"],
                    "sha256": entry["sha256"],
                },
            )
    await runtime.roster.update(clone.agent_id, {"workspace_dir": str(stage)})
