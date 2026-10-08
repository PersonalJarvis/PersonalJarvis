"""Private, agent-scoped state transfer between the owner's computers.

Unlike public templates this carries operational history. It never includes
provider logins, browser profiles, the global configuration, or other agents.
The caller must fence admissions and verify idle before taking the snapshot.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 10_000
_BLOCKED = {
    ".env",
    ".git",
    ".ssh",
    "credentials",
    "credentials.json",
    "auth.json",
    "browser",
    "browser-profile",
    "keyring",
    ".control_api_key",
}


class CloudBundleError(ValueError):
    """The selected agent cannot be moved without losing important state."""


def _rows(path: Path, table: str, where: str, values: tuple[Any, ...]) -> list[dict]:
    if not path.exists():
        return []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            return []
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE {where}", values)]  # noqa: S608 -- internal table/predicate literals
    finally:
        conn.close()


def _files(root: Path, prefix: str, files: dict[str, str]) -> None:
    from jarvis.agentic_ide.remote import is_secret_name

    if not root.exists():
        return
    if root.is_symlink():
        raise CloudBundleError("Linked workspace or memory directories cannot be moved safely.")
    encoded_bytes = sum(len(value) for value in files.values())
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(is_secret_name(part) for part in relative.parts):
            continue
        if any(
            part.lower() in _BLOCKED or part.lower().startswith(".env.") for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise CloudBundleError(
                "The workspace contains a symbolic link; remove it before moving."
            )
        if not path.is_file():
            continue
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            raise CloudBundleError("The workspace contains a private-key file; move it out first.")
        if path.stat().st_size > MAX_BYTES:
            raise CloudBundleError("An agent file exceeds the 64 MiB transfer limit.")
        key = str(PurePosixPath(prefix, relative.as_posix()))
        files[key] = base64.b64encode(path.read_bytes()).decode("ascii")
        encoded_bytes += len(files[key])
        if len(files) > MAX_FILES or encoded_bytes > MAX_BYTES * 4 // 3:
            raise CloudBundleError("The agent's files exceed the 64 MiB transfer limit.")


async def build_bundle(runtime: Any, agent_id: str) -> dict[str, Any]:
    """Snapshot one idle agent; all database predicates remain owner scoped."""
    agent = await runtime.roster.get(agent_id)
    if agent is None or agent_id == "jarvis":
        raise CloudBundleError("Choose a teammate; the desktop's lead cannot be moved.")
    if str(agent.runtime) != "jarvis":
        raise CloudBundleError("This runtime's private state cannot yet be transferred.")
    from jarvis.agent_chat.service import resolve_runner

    runner = resolve_runner(agent.provider, surface="society", account_id=agent.account_id)
    if not runner.endswith("-cli"):
        raise CloudBundleError("Select a subscription CLI seat before moving this agent.")
    if agent.computer_id:
        raise CloudBundleError("Bring this agent's existing SSH workspace back before hosting it.")
    if agent.browser_mode == "attach":
        raise CloudBundleError("An attached desktop browser cannot work while this PC is off.")
    chat = runtime.chat_service()
    if chat is None:
        raise CloudBundleError("The agent chat service is unavailable.")
    sid = agent.session_id

    def owns(value: str) -> bool:
        return value == sid or value.startswith(sid + ":")

    if await runtime.store.kill_switch():
        raise CloudBundleError("Release the agent emergency stop before moving an agent.")
    reservations = getattr(chat, "running_session_ids", lambda: [])()
    if any(owns(value) for value in reservations):
        raise CloudBundleError("Wait until the agent's current turn and routines finish.")
    scheduler = getattr(runtime, "scheduler", None)
    if scheduler is not None and scheduler.active_runs(agent_id):
        raise CloudBundleError("Wait until the agent's active missions finish.")
    if any(owns(value[0]) for value in chat.store.open_turns()):
        raise CloudBundleError(
            "A prior agent turn has no durable outcome; recover it before moving."
        )
    queues = getattr(chat, "_society_send_queues", {})
    if any(owns(key) and value.items for key, value in queues.items()):
        raise CloudBundleError("Wait until the agent's queued messages finish.")
    sessions = [
        s
        for s in chat.store.list_sessions(surface="society", limit=100_000)
        if s.session_id == sid or s.session_id.startswith(sid + ":")
    ]
    if any(chat.is_running(s.session_id) for s in sessions):
        raise CloudBundleError("Wait until the agent's current turn and routines finish.")
    data_dir = Path(runtime._data_dir)  # noqa: SLF001 -- runtime-owned transfer
    society_db = runtime.store.path
    rooms = _rows(society_db, "society_rooms", "state IN ('queued','running')", ())
    if any(agent_id in json.loads(row["members_json"]) for row in rooms):
        raise CloudBundleError("Finish this agent's active team discussion before moving it.")
    quests = _rows(society_db, "society_quests", "agent_id=?", (agent_id,))
    if any(row["state"] in {"open", "assigned", "running"} for row in quests):
        raise CloudBundleError("Finish this agent's active quest before moving it.")
    chat_db = Path(chat.store._path)  # noqa: SLF001 -- read-only consistent SQL snapshots
    task_store, _ = runtime.task_services()
    task_db = Path(task_store._db_path) if task_store else data_dir / "jarvis.db"  # noqa: SLF001
    tasks = _rows(task_db, "tasks", "1=1", ())
    tasks = [
        row for row in tasks if f"agent:{agent_id}" in json.loads(row["spec_json"]).get("tags", [])
    ]
    for row in tasks:
        spec = json.loads(row["spec_json"])
        if row["state"] == "running":
            raise CloudBundleError("Wait until the agent's running routine finishes.")
        if spec["trigger"]["type"] not in {"every", "calendar", "cron"}:
            raise CloudBundleError(
                "Only recurring time schedules can move; one-shot and event routines stay here."
            )
        if spec["action"]["kind"] != "agent":
            raise CloudBundleError("Only agent routines can move with their owner.")
        if spec["action"].get("plugin_grants"):
            raise CloudBundleError(
                "Reconnect this routine's plugins on the server before moving it."
            )
        if "autonomous" in spec.get("tags", []):
            raise CloudBundleError(
                "This routine depends on a separate originating chat's permissions."
            )
    tables: dict[str, dict[str, list[dict]]] = {
        "society.db": {
            "society_agents": _rows(society_db, "society_agents", "agent_id=?", (agent_id,)),
            "society_events": _rows(
                society_db, "society_events", "from_agent=? OR to_agent=?", (agent_id, agent_id)
            ),
            "knowledge": _rows(society_db, "knowledge", "agent_id=?", (agent_id,)),
            "approvals": _rows(society_db, "approvals", "agent_id=?", (agent_id,)),
            "society_deliveries": _rows(
                society_db,
                "society_deliveries",
                "event_id IN (SELECT event_id FROM society_events "
                "WHERE from_agent=? OR to_agent=?)",
                (agent_id, agent_id),
            ),
        },
        "agent_chat.db": {},
        "society-conversations.db": {},
        "jarvis.db": {"tasks": tasks, "task_steps": []},
    }
    if any(row["state"] in {"pending", "approved"} for row in tables["society.db"]["approvals"]):
        raise CloudBundleError("Resolve the agent's pending approvals before moving it.")
    if any(row["status"] == "queued" for row in tables["society.db"]["society_deliveries"]):
        raise CloudBundleError("Wait until the agent's pending messages have been delivered.")
    # Transfer a local execution owner on the destination, not an SSH recursion.
    record = tables["society.db"]["society_agents"][0]
    record.update(
        computer_id=None,
        parent_agent_id=None,
        account_id="",
        workspace_dir=f"society/{agent_id}/workspace",
    )
    for table in ("agent_chat_sessions", "agent_chat_events", "agent_chat_permission_overrides"):
        tables["agent_chat.db"][table] = _rows(
            chat_db,
            table,
            "session_id=? OR substr(session_id,1,?)=?",
            (sid, len(sid) + 1, sid + ":"),
        )
    for row in tables["agent_chat.db"]["agent_chat_sessions"]:
        # Vendor session ids refer to this machine's native CLI store. The
        # canonical event log reconstructs prior dialogue on the destination.
        row.update(vendor_session=None, account_id="", cwd=f"society/{agent_id}/workspace")
    owners = _rows(chat_db, "agent_chat_thread_owners", "owner_agent=?", (agent_id,))
    if owners:
        raise CloudBundleError("This agent owns coding threads; move or close them first.")
    for table in ("messages", "checkpoints", "reviews"):
        tables["society-conversations.db"][table] = _rows(
            data_dir / "society-conversations.db",
            table,
            "session=? OR substr(session,1,?)=?",
            (sid, len(sid) + 1, sid + ":"),
        )
    if any(row["status"] == "pending" for row in tables["society-conversations.db"]["reviews"]):
        raise CloudBundleError("Wait until the agent's memory review finishes.")
    for task in tasks:
        tables["jarvis.db"]["task_steps"].extend(
            _rows(task_db, "task_steps", "task_id=?", (task["id"],))
        )
        spec = json.loads(task["spec_json"])
        spec["action"]["account_id"] = ""
        task["spec_json"] = json.dumps(spec, ensure_ascii=False)
    files: dict[str, str] = {}
    from .chat_binding import _workspace

    await asyncio.to_thread(
        _files,
        Path(_workspace(runtime.config(), agent)),
        f"data/society/{agent_id}/workspace",
        files,
    )
    await asyncio.to_thread(
        _files, data_dir / "society" / agent_id / "skills", f"data/society/{agent_id}/skills", files
    )
    vault = runtime.memory.root()
    await asyncio.to_thread(
        _files, vault / "society" / agent_id, f"data/wiki/society/{agent_id}", files
    )
    if str(agent.knowledge_scope) == "shared":
        await asyncio.to_thread(
            _files, vault / "society" / "shared", "data/wiki/society/shared", files
        )
    return {"version": VERSION, "agent_id": agent_id, "tables": tables, "files": files}


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    allowed = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
    for row in rows:
        if not set(row) <= allowed:
            raise CloudBundleError(f"The destination schema for {table} is incompatible.")
        keys = list(row)
        marks = ",".join("?" for _ in keys)
        conn.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({marks})",  # noqa: S608 -- validated schema
            tuple(row[k] for k in keys),
        )


async def import_bundle(root: Path, bundle: dict[str, Any]) -> None:
    """Import once into an empty dedicated instance, with routines initially paused."""
    from jarvis.agent_chat.store import AgentChatStore
    from jarvis.tasks.store import TaskStore

    from .conversation import ConversationArchive
    from .store import SocietyStore

    if bundle.get("version") != VERSION:
        raise CloudBundleError("The cloud transfer format is incompatible.")
    agent_id = str(bundle.get("agent_id", ""))
    if not agent_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in agent_id):
        raise CloudBundleError("Invalid agent identity.")
    root = await asyncio.to_thread(root.resolve)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    data = root / "data"
    data.mkdir(mode=0o700, exist_ok=True)
    if (data / "society.db").exists():
        raise CloudBundleError("This destination already contains an agent transfer.")
    files = bundle.get("files", {})
    total = 0
    decoded = []
    if len(files) > MAX_FILES:
        raise CloudBundleError("Too many transfer files.")
    for name, content in files.items():
        rel = PurePosixPath(name)
        target = root.joinpath(*rel.parts).resolve()
        if rel.is_absolute() or ".." in rel.parts or root not in target.parents:
            raise CloudBundleError("A transfer file leaves its destination.")
        prefixes = (
            f"data/society/{agent_id}/workspace/",
            f"data/society/{agent_id}/skills/",
            f"data/wiki/society/{agent_id}/",
            "data/wiki/society/shared/",
        )
        if not name.startswith(prefixes):
            raise CloudBundleError("A transfer file is outside the selected agent.")
        raw = base64.b64decode(content, validate=True)
        total += len(raw)
        if total > MAX_BYTES:
            raise CloudBundleError("Transfer files exceed the 64 MiB limit.")
        decoded.append((target, raw))
    stores = [SocietyStore(data / "society.db"), TaskStore(data / "jarvis.db")]
    await stores[0].open()
    await stores[0].close()
    await stores[1].init()
    await stores[1].close()
    AgentChatStore(data / "agent_chat.db").close()
    ConversationArchive(data / "society-conversations.db").close()
    expected = {
        "society.db": {
            "society_agents",
            "society_events",
            "knowledge",
            "approvals",
            "society_deliveries",
        },
        "agent_chat.db": {
            "agent_chat_sessions",
            "agent_chat_events",
            "agent_chat_permission_overrides",
        },
        "society-conversations.db": {"messages", "checkpoints", "reviews"},
        "jarvis.db": {"tasks", "task_steps"},
    }
    if set(bundle["tables"]) != set(expected):
        raise CloudBundleError("Invalid transfer databases.")
    for filename, tables in bundle["tables"].items():
        if set(tables) != expected[filename]:
            raise CloudBundleError("Invalid transfer tables.")
        with sqlite3.connect(data / filename) as conn:
            if filename == "society.db":
                # Import historical messages inertly. The normal insert trigger
                # would otherwise queue every old message for another execution.
                conn.execute("DROP TRIGGER IF EXISTS society_queue_message")
            for table, rows in tables.items():
                if table == "agent_chat_sessions":
                    rows = [
                        {**row, "cwd": str(data / "society" / agent_id / "workspace")}
                        for row in rows
                    ]
                if table == "tasks":
                    rows = [
                        {
                            **row,
                            "state": "paused"
                            if row["state"] in {"scheduled", "pending"}
                            else row["state"],
                        }
                        for row in rows
                    ]
                _insert(conn, table, rows)
            if filename == "society.db":
                conn.execute(
                    "INSERT OR REPLACE INTO society_meta(key,value) VALUES ('kill_switch','1')"
                )
    # Recreate the delivery trigger only after history and receipts are committed.
    society = SocietyStore(data / "society.db")
    await society.open()
    await society.close()
    for target, raw in decoded:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    for child in root.rglob("*"):
        child.chmod(0o700 if child.is_dir() else 0o600)
    # Rebuild the conversation search projection after inserting archived rows.
    ConversationArchive(data / "society-conversations.db").close()
