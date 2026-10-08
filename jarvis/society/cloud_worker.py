"""Detached, loopback-only host using the ordinary Jarvis agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def readiness(bundle: dict[str, Any]) -> dict[str, Any]:
    """Inspect an installed CLI and its native login without a model request."""
    from jarvis import agent_accounts
    from jarvis.agent_chat.catalog import provider_row
    from jarvis.agent_chat.service import resolve_runner
    from jarvis.agentic_ide.session import agent_argv

    record = bundle["tables"]["society.db"]["society_agents"][0]
    providers = {record["provider"]}
    for row in bundle["tables"]["jarvis.db"]["tasks"]:
        providers.add(json.loads(row["spec_json"])["action"].get("provider") or record["provider"])
    accounts = {}
    for provider in providers:
        row = provider_row(provider)
        runner = resolve_runner(provider, surface="society")
        if row is None or not row.agent or not runner.endswith("-cli"):
            raise ValueError("Select a subscription CLI seat before moving this agent.")
        if agent_argv(row.agent) is None:
            raise ValueError(f"Install {row.label} on the selected server first.")
        account = agent_accounts.active_account(row.agent)
        state = agent_accounts.describe(account)
        if not state.connected or state.mode != "subscription":
            raise ValueError(f"Connect a subscription login for {row.label} on the server first.")
        accounts[provider] = account.id
    return {
        "ready": True,
        "provider": record["provider"],
        "model": record["model"],
        "accounts": accounts,
    }


async def serve(root: Path) -> None:
    if (root / "aborted.json").exists():
        raise RuntimeError("This cloud preparation was aborted.")
    import uvicorn

    from jarvis.brain.factory import build_default_brain
    from jarvis.core import control_key
    from jarvis.core.config import load_config
    from jarvis.ui.web.agent_chat_routes import _service_from_state
    from jarvis.ui.web.server import WebServer

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    bundle = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    token = (root / "access.token").read_text(encoding="utf-8").strip()
    server = WebServer(load_config())
    server.app.state.native_file_actions = False
    server.app.state.brain = await asyncio.to_thread(
        build_default_brain, bus=server.bus, tier="router"
    )
    control_key.ensure_control_key()
    await server.start(start_serving=False)
    service = await asyncio.to_thread(_service_from_state, server.app.state)
    if service is None:
        raise RuntimeError("The hosted chat service did not start.")
    runtime = server.app.state.society or server.app.state.society_factory()
    server.app.state.society = runtime
    runtime.memory._vault_root = lambda: root / "data" / "wiki"  # noqa: SLF001 -- isolated host
    await runtime.ensure_started()
    await runtime.roster.update("jarvis", {"state": "paused"})
    active_file = root / "active.json"
    lock = asyncio.Lock()

    async def activate() -> bool:
        async with lock:
            if (root / "aborted.json").exists():
                return False
            if active_file.exists():
                return True
            original = bundle["tables"]["society.db"]["society_agents"][0]
            await runtime.roster.update(manifest["agent_id"], {"state": original["state"]})
            # No scheduler hydration until all transferred rows have their exact
            # state/due time back. Paused routines remain paused.
            with sqlite3.connect(root / "data" / "jarvis.db") as conn:
                for row in bundle["tables"]["jarvis.db"]["tasks"]:
                    conn.execute(
                        "UPDATE tasks SET state=?, due_at_ns=? WHERE id=? AND state='paused'",
                        (row["state"], row["due_at_ns"], row["id"]),
                    )
            write_json(active_file, {"transfer_id": manifest["transfer_id"], "active": True})
            await runtime.store.set_kill_switch(False)
            await server.app.state.task_scheduler.hydrate()
            return True

    async def app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await server.stop()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        headers = dict(scope.get("headers", ()))
        supplied = headers.get(b"authorization", b"")
        if not secrets.compare_digest(supplied, ("Bearer " + token).encode()):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
            else:
                await _json(send, 401, {"detail": "Unauthorized"})
            return
        path = scope.get("path", "")
        if path == "/cloud/status":
            await _json(
                send,
                200,
                {
                    "ready": True,
                    "active": active_file.exists(),
                    "transfer_id": manifest["transfer_id"],
                    "agent_id": manifest["agent_id"],
                },
            )
            return
        if path == "/cloud/activate" and scope.get("method") == "POST":
            if not await activate():
                await _json(send, 409, {"detail": "The dormant host was already aborted."})
                return
            await _json(send, 200, {"active": True, "transfer_id": manifest["transfer_id"]})
            return
        if path == "/cloud/abort" and scope.get("method") == "POST":
            async with lock:
                if active_file.exists():
                    await _json(send, 409, {"detail": "An active host cannot be aborted."})
                    return
                write_json(root / "aborted.json", {"transfer_id": manifest["transfer_id"]})
                await _json(send, 200, {"aborted": True, "transfer_id": manifest["transfer_id"]})
                runner.should_exit = True
            return
        if not active_file.exists():
            await _json(send, 409, {"detail": "The cloud agent is not active yet."})
            return
        # The dedicated host token never unlocks another instance. Translate it
        # to the remote user's local control credential only inside this process.
        forwarded = dict(scope)
        forwarded["headers"] = [
            (name, value)
            for name, value in scope.get("headers", ())
            if name not in {b"authorization", b"cookie", b"origin"}
        ]
        forwarded["headers"].append(
            (b"authorization", ("Bearer " + control_key.get_control_key()).encode())
        )
        await server.app(forwarded, receive, send)

    config = uvicorn.Config(app, host="127.0.0.1", port=manifest["port"], log_level="warning")
    runner = uvicorn.Server(config)

    async def expire_preparation() -> None:
        await asyncio.sleep(600)
        async with lock:
            if not active_file.exists():
                write_json(root / "aborted.json", {"transfer_id": manifest["transfer_id"]})
                runner.should_exit = True

    expiry = asyncio.create_task(expire_preparation())
    try:
        await runner.serve()
    finally:
        expiry.cancel()
        await asyncio.gather(expiry, return_exceptions=True)


async def _json(send: Any, status: int, value: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps(value).encode()})


async def prepare(root: Path) -> dict[str, Any]:
    from .cloud_bundle import import_bundle

    if (root / "aborted.json").exists():
        raise RuntimeError("This cloud preparation was aborted.")
    bundle = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    result = readiness(bundle)
    accounts = result["accounts"]
    owner = bundle["tables"]["society.db"]["society_agents"][0]
    owner["account_id"] = accounts[owner["provider"]]
    for session in bundle["tables"]["agent_chat.db"]["agent_chat_sessions"]:
        session["account_id"] = accounts.get(session["provider"], "")
    for task in bundle["tables"]["jarvis.db"]["tasks"]:
        spec = json.loads(task["spec_json"])
        spec["action"]["account_id"] = accounts[spec["action"].get("provider") or owner["provider"]]
        task["spec_json"] = json.dumps(spec)
    write_json(root / "bundle.json", bundle)
    await import_bundle(root, bundle)
    # Quiescent until the explicit activation exchange succeeds.
    with sqlite3.connect(root / "data" / "society.db") as conn:
        conn.execute(
            "UPDATE society_agents SET state='paused' WHERE agent_id=?", (bundle["agent_id"],)
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "serve"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    os.environ["JARVIS_CONFIG"] = str(root / "jarvis.toml")
    os.environ["JARVIS_DATA_DIR"] = str(root / "data")
    os.environ["JARVIS__MEMORY__DATA_DIR"] = str(root / "data")
    os.environ.pop("JARVIS_INSTANCE", None)
    import tomlkit

    from jarvis.core.config_writer import _WRITE_LOCK, _atomic_write

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not (root / "jarvis.toml").exists():
        config = {
            "memory": {"data_dir": str(root / "data")},
            "wiki_integration": {"vault_root": str(root / "data" / "wiki")},
            "ui": {"admin_api_port": manifest["port"]},
            "autostart": {"enabled": False},
        }
        with _WRITE_LOCK:
            _atomic_write(root / "jarvis.toml", tomlkit.dumps(config))
    if args.operation == "prepare":
        result = asyncio.run(prepare(root))
        print(json.dumps(result), flush=True)
    else:
        asyncio.run(serve(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
