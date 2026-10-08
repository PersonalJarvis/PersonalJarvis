"""Durable ownership transfer to a detached agent host on a connected VPS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from jarvis.core.http_pool import HttpClientPool

from .cloud_bundle import CloudBundleError, build_bundle

log = logging.getLogger(__name__)
_LOCKS: dict[str, asyncio.Lock] = {}
_HTTP = HttpClientPool(timeout_s=30)
STATES = {"preparing", "ready", "activating", "active", "uncertain"}
_CONNECT_SLOTS: WeakKeyDictionary = WeakKeyDictionary()


def ownership_lock(data_dir: Path, agent_id: str) -> asyncio.Lock:
    """Serialize final dispatch admission with creation of the ownership fence."""
    key = str(Path(data_dir).resolve()) + ":" + agent_id
    return _LOCKS.setdefault(key, asyncio.Lock())


@asynccontextmanager
async def _snapshot_ownership(runtime: Any, data_dir: Path, agent_id: str):
    """Take review before ownership, releasing review immediately after snapshot."""
    review = getattr(runtime, "_review_lock", None)
    review_held = False
    if review is not None:
        await review.acquire()
        review_held = True

    def snapshot_done() -> None:
        nonlocal review_held
        if review_held:
            review.release()
            review_held = False

    try:
        async with ownership_lock(data_dir, agent_id):
            yield snapshot_done
    finally:
        snapshot_done()


class CloudHostError(RuntimeError):
    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _path(data_dir: Path, agent_id: str) -> Path:
    if not agent_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in agent_id):
        raise CloudHostError("Invalid agent identity.", status=422)
    return Path(data_dir) / "cloud-agents" / f"{agent_id}.json"


def placement_for(data_dir: Path, agent_id: str) -> dict[str, Any] | None:
    """A durable fence. An unreadable locator must never restore local ownership."""
    path = _path(data_dir, agent_id)
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # No ownership receipt is the normal state of a local-only agent.
        return None
    except (OSError, ValueError):
        log.warning("Cloud locator for %s is unreadable; local execution remains fenced", agent_id)
        return {
            "agent_id": agent_id,
            "state": "uncertain",
            "detail": "Cloud ownership needs recovery.",
        }
    if not isinstance(row, dict) or row.get("state") not in STATES:
        return {
            "agent_id": agent_id,
            "state": "uncertain",
            "detail": "Cloud ownership needs recovery.",
        }
    return row


def placements_for(data_dir: Path) -> list[dict[str, Any]]:
    folder = Path(data_dir) / "cloud-agents"
    return [
        row
        for path in sorted(folder.glob("*.json"))
        if (row := placement_for(data_dir, path.stem)) is not None
    ]


def _save(data_dir: Path, row: dict[str, Any]) -> None:
    path = _path(data_dir, row["agent_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    row["updated_ms"] = int(time.time() * 1000)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class CloudHost:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.data_dir = Path(runtime._data_dir)  # noqa: SLF001 -- service owned by this runtime
        if not hasattr(runtime, "_cloud_tunnels"):
            runtime._cloud_tunnels = {}  # noqa: SLF001 -- runtime-owned connection lifetime
            runtime._cloud_tunnel_lock = asyncio.Lock()  # noqa: SLF001
            runtime._cloud_tunnel_retry = {}  # noqa: SLF001

    def placement(self, agent_id: str) -> dict[str, Any] | None:
        return placement_for(self.data_dir, agent_id)

    async def remember_agent(self, agent_id: str, agent: dict[str, Any]) -> None:
        """Keep the desktop roster projection current without admitting local execution."""
        if agent.get("agent_id") != agent_id:
            raise CloudHostError("The server returned another agent's identity.")
        async with ownership_lock(self.data_dir, agent_id):
            row = self.placement(agent_id)
            if row:
                row["agent_snapshot"] = {**row.get("agent_snapshot", {}), **agent}
                _save(self.data_dir, row)

    async def aclose(self) -> None:
        """Close the runtime's owned forwards; remote processes continue working."""
        self.runtime._cloud_tunnels_closed = True  # noqa: SLF001
        async with self.runtime._cloud_tunnel_lock:  # noqa: SLF001
            cached = list(self.runtime._cloud_tunnels.values())  # noqa: SLF001
            self.runtime._cloud_tunnels.clear()  # noqa: SLF001
        for entry in cached:
            await entry["stack"].aclose()

    @asynccontextmanager
    async def tunnel(self, agent_id: str):
        """Authenticated HTTP/websocket access, through the existing pinned SSH transport."""
        from jarvis.computers.service import get_service
        from jarvis.core.config import get_secret

        row = self.placement(agent_id)
        if not row or not row.get("port"):
            raise CloudHostError("The cloud agent has no reachable host.")
        token = get_secret("cloud_agent_" + row["transfer_id"])
        if not token:
            raise CloudHostError("The cloud agent connection credential is unavailable.")
        key = row["transfer_id"]
        async with self.runtime._cloud_tunnel_lock:  # noqa: SLF001
            if getattr(self.runtime, "_cloud_tunnels_closed", False):
                raise CloudHostError("The desktop connection is stopping.")
            cache = self.runtime._cloud_tunnels  # noqa: SLF001
            entry = cache.get(key)
            if entry and entry["conn"].is_closed():
                await entry["stack"].aclose()
                cache.pop(key, None)
                entry = None
            if entry is None:
                loop = asyncio.get_running_loop()
                retries = self.runtime._cloud_tunnel_retry  # noqa: SLF001
                if loop.time() < retries.get(key, 0):
                    raise CloudHostError(
                        "The cloud connection is recovering. Try again shortly.", status=503
                    )
                slots = _CONNECT_SLOTS.setdefault(loop, asyncio.Semaphore(3))
                stack = AsyncExitStack()
                try:
                    async with slots:
                        opened = await stack.enter_async_context(
                            get_service().session(row["computer_id"])
                        )
                        forward = await opened.conn.forward_local_port(
                            "127.0.0.1",
                            0,
                            "127.0.0.1",
                            int(row["port"]),
                        )

                    async def close_forward() -> None:
                        forward.close()
                        await forward.wait_closed()

                    stack.push_async_callback(close_forward)
                    entry = {
                        "stack": stack,
                        "conn": opened.conn,
                        "url": f"http://127.0.0.1:{forward.get_port()}",
                    }
                    cache[key] = entry
                    retries.pop(key, None)
                except BaseException:
                    retries[key] = loop.time() + 2 + secrets.randbelow(1000) / 1000
                    await stack.aclose()
                    raise
        yield entry["url"], token

    async def request(
        self, agent_id: str, method: str, path: str, body: Any = None
    ) -> tuple[int, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise CloudHostError("Invalid cloud request path.", status=422)
        async with self.tunnel(agent_id) as (url, token):
            client = _HTTP.client()
            response = await client.request(
                method,
                url + path,
                json=body,
                headers={"Authorization": "Bearer " + token},
                timeout=30,
            )
            try:
                result = response.json()
            except ValueError:
                # A non-JSON upstream failure stays visible in the response detail.
                result = {"detail": response.text[:2000]}
            return response.status_code, result

    async def status(self, agent_id: str) -> dict[str, Any] | None:
        async with ownership_lock(self.data_dir, agent_id):
            row = self.placement(agent_id)
            if not row or not row.get("port"):
                return row
            code, remote = await self.request(agent_id, "GET", "/cloud/status")
            if (
                code == 200
                and remote.get("transfer_id") == row["transfer_id"]
                and remote.get("active")
            ):
                row.update(state="active", detail="")
                _save(self.data_dir, row)
            return row

    async def _restore_routines(self, row: dict[str, Any]) -> None:
        task_store, _ = self.runtime.task_services()
        originals = row.get("routines", [])
        for task in originals:
            await task_store.update_state(task["id"], task["state"])
            await task_store.set_next_due(task["id"], task["due_at_ns"])

    async def _hydrate_restored(self, row: dict[str, Any]) -> None:
        _, scheduler = self.runtime.task_services()
        if row.get("routines") and scheduler:
            await scheduler.hydrate()

    async def cancel_preparation(self, agent_id: str) -> dict[str, Any]:
        """Recover preparation only after proving no remote execution owner is active."""
        async with ownership_lock(self.data_dir, agent_id):
            row = self.placement(agent_id)
            if row is None:
                return {"cancelled": True, "agent_id": agent_id}
            if row.get("state") == "active":
                raise CloudHostError(
                    "The server owns this agent; an active handoff cannot be cancelled."
                )
            if row.get("state") in {"preparing", "ready"}:
                await self._abort_unactivated(row)
            elif row.get("port"):
                code, remote = await self.request(agent_id, "GET", "/cloud/status")
                if code != 200 or remote.get("transfer_id") != row.get("transfer_id"):
                    raise CloudHostError(
                        "The server could not confirm that this preparation is inactive."
                    )
                if remote.get("active"):
                    row.update(state="active", detail="")
                    _save(self.data_dir, row)
                    raise CloudHostError(
                        "The server already owns this agent. Local work stays paused."
                    )
                code, result = await self.request(agent_id, "POST", "/cloud/abort", {})
                if (
                    code != 200
                    or result.get("transfer_id") != row["transfer_id"]
                    or not result.get("aborted")
                ):
                    raise CloudHostError(
                        "The server did not acknowledge aborting its dormant host."
                    )
            elif row.get("state") != "preparing":
                raise CloudHostError(
                    "Cloud ownership is uncertain; reconnect the server before recovery."
                )
            await self._discard_prepared(row)
            await self._restore_routines(row)
            _path(self.data_dir, agent_id).unlink(missing_ok=True)
            await self._hydrate_restored(row)
            return {"cancelled": True, "agent_id": agent_id}

    async def _abort_unactivated(self, row: dict[str, Any]) -> None:
        """Persist an abort even when upload/startup crashed before HTTP existed."""
        if not row.get("remote_root"):
            return
        from jarvis.computers import remote_os
        from jarvis.computers.service import get_service

        async with get_service().session(row["computer_id"]) as opened:
            host = await remote_os.remote_host(row["computer_id"], opened)
            expected = f"{host.home}/jarvis-agents/hosted/{row['transfer_id']}"
            if row["remote_root"] != expected:
                raise CloudHostError("The remote ownership path could not be verified.")
            root = shlex.quote(expected)
            session = shlex.quote("jarvis-cloud-" + row["transfer_id"])
            script = (
                f"umask 077\nmkdir -p {root} || exit 74\n"
                f"test ! -e {root}/active.json || exit 73\n"
                f"printf '%s' '{{\"aborted\":true}}' > {root}/aborted.json || exit 74\n"
                f"if tmux has-session -t {session} 2>/dev/null; then "
                f"tmux kill-session -t {session} || exit 74; fi\n"
            )
            result = await remote_os.run_script(opened, host, script, timeout_s=30)
            if result.exit_status != 0:
                raise CloudHostError(
                    "The server could not prove that its dormant preparation stopped."
                )

    async def handoff(self, agent_id: str, computer_id: str) -> dict[str, Any]:
        """Prepare, fence, snapshot, verify readiness, then activate exactly one owner.

        A lost activation response is uncertain, never an excuse to replay the
        work locally. A pre-activation failure can safely remove the local fence.
        """
        async with _snapshot_ownership(self.runtime, self.data_dir, agent_id) as snapshot_done:
            if self.placement(agent_id):
                raise CloudHostError(
                    "This agent already has a cloud transfer; reconnect to its host."
                )
            row = {
                "agent_id": agent_id,
                "computer_id": computer_id,
                "state": "preparing",
                "transfer_id": uuid4().hex,
                "remote_root": "",
                "port": 0,
                "detail": "",
            }
            _save(self.data_dir, row)
            activation_attempted = False
            paused: list[str] = []
            try:
                # Fence exists before the final idle checks in build_bundle.
                bundle = await build_bundle(self.runtime, agent_id)
                snapshot_done()
                row["routines"] = [
                    {"id": task["id"], "state": task["state"], "due_at_ns": task["due_at_ns"]}
                    for task in bundle["tables"]["jarvis.db"]["tasks"]
                    if task["state"] in {"scheduled", "pending"}
                ]
                _save(self.data_dir, row)
                _, scheduler = self.runtime.task_services()
                if scheduler:
                    for task in bundle["tables"]["jarvis.db"]["tasks"]:
                        if task["state"] in {"scheduled", "pending"} and task["trigger_type"] in {
                            "every",
                            "calendar",
                            "cron",
                        }:
                            await scheduler.pause(task["id"])
                            paused.append(task["id"])
                row["paused_routines"] = paused
                _save(self.data_dir, row)
                await self._prepare(row, bundle)
                row["state"] = "ready"
                _save(self.data_dir, row)
                code, ready = await self.request(agent_id, "GET", "/cloud/status")
                if (
                    code != 200
                    or ready.get("transfer_id") != row["transfer_id"]
                    or not ready.get("ready")
                ):
                    raise CloudHostError("The server did not confirm this transfer is ready.")
                if await self.runtime.store.kill_switch():
                    raise CloudHostError(
                        "The emergency stop was engaged; the cloud agent was not activated."
                    )
                row["state"] = "activating"
                _save(self.data_dir, row)
                activation_attempted = True
                code, active = await self.request(agent_id, "POST", "/cloud/activate", {})
                if (
                    code != 200
                    or active.get("transfer_id") != row["transfer_id"]
                    or not active.get("active")
                ):
                    raise CloudHostError(
                        "The server has not confirmed activation; local work stays paused."
                    )
                row["state"] = "active"
                _save(self.data_dir, row)
                return row
            except BaseException as exc:
                if activation_attempted:
                    row.update(
                        state="uncertain",
                        detail="Reconnect to confirm cloud ownership; local work is paused.",
                    )
                    _save(self.data_dir, row)
                else:
                    restored = True
                    await self._discard_prepared(row)
                    try:
                        await self._restore_routines(row)
                    except Exception:
                        restored = False
                        log.exception(
                            "Could not restore agent routines after a refused cloud transfer"
                        )
                        row.update(
                            state="uncertain", detail="Local routine recovery needs attention."
                        )
                        _save(self.data_dir, row)
                    if restored:
                        _path(self.data_dir, agent_id).unlink(missing_ok=True)
                        await self._hydrate_restored(row)
                if isinstance(exc, (CloudHostError, asyncio.CancelledError)):
                    raise
                if isinstance(exc, CloudBundleError):
                    raise CloudHostError(str(exc)) from exc
                log.warning("Cloud transfer for %s failed (%s)", agent_id, type(exc).__name__)
                raise CloudHostError(
                    "The cloud transfer failed. Local work was preserved.", status=502
                ) from exc

    async def _discard_prepared(self, row: dict[str, Any]) -> None:
        """A refused transfer must not leave its dormant host process running."""
        if not row.get("remote_root"):
            return
        from jarvis.computers import remote_os
        from jarvis.computers.service import get_service

        try:
            entry = self.runtime._cloud_tunnels.pop(row["transfer_id"], None)  # noqa: SLF001
            if entry:
                await entry["stack"].aclose()
            async with get_service().session(row["computer_id"]) as opened:
                host = await remote_os.remote_host(row["computer_id"], opened)
                session = shlex.quote("jarvis-cloud-" + row["transfer_id"])
                await remote_os.run_script(
                    opened,
                    host,
                    f"tmux has-session -t {session} 2>/dev/null && "
                    f"tmux kill-session -t {session}\n",
                    timeout_s=15,
                )
        except Exception:
            # A dormant host remains fenced by its own kill switch, even when
            # the failed connection prevents this best-effort process cleanup.
            log.warning("Dormant cloud host cleanup was unavailable", exc_info=True)

    async def _prepare(self, row: dict[str, Any], bundle: dict[str, Any]) -> None:
        from jarvis.computers import remote_os
        from jarvis.computers.service import get_service
        from jarvis.core.config import set_secret

        async with get_service().session(row["computer_id"]) as opened:
            if get_service().get(row["computer_id"]).kind == "local_vm":
                raise CloudHostError("A local virtual machine stops with this PC; choose a server.")
            host = await remote_os.remote_host(row["computer_id"], opened)
            if host.windows:
                raise CloudHostError(
                    "Autonomous agent hosting currently requires a Linux or macOS server."
                )
            probe = (
                "command -v tmux >/dev/null || exit 71\n"
                "for p in python3 python; do\n"
                '  command -v "$p" >/dev/null || continue\n'
                '  "$p" -c \'import jarvis,sys,socket,json; '
                's=socket.socket();s.bind(("127.0.0.1",0)); '
                'print(json.dumps({"python":sys.executable,"port":s.getsockname()[1]}))\''
                " && exit 0\n"
                "done\nexit 72\n"
            )
            checked = await remote_os.run_script(opened, host, probe, timeout_s=30)
            if checked.exit_status != 0:
                raise CloudHostError(
                    "Install Jarvis in the server's Python environment and tmux "
                    "before hosting agents."
                )
            info = json.loads(checked.stdout.strip().splitlines()[-1])
            root = f"{host.home}/jarvis-agents/hosted/{row['transfer_id']}"
            private_root = await remote_os.run_script(
                opened,
                host,
                f"umask 077\nmkdir -p {shlex.quote(root)} && chmod 700 {shlex.quote(root)}\n",
                timeout_s=30,
            )
            if private_root.exit_status != 0:
                raise CloudHostError("The server could not create a private agent folder.")
            row.update(remote_root=root, port=int(info["port"]))
            _save(self.data_dir, row)
            token = secrets.token_urlsafe(32)
            if not set_secret("cloud_agent_" + row["transfer_id"], token):
                raise CloudHostError("The cloud connection credential could not be saved.")
            # Ship only the three reviewed hosting modules, never a checkout,
            # machine configuration, credentials, or the desktop's account files.
            for name in ("cloud_bundle.py", "cloud_worker.py"):
                await remote_os.upload_text(
                    opened,
                    host,
                    f"{root}/modules/{name}",
                    Path(__file__).with_name(name).read_text(encoding="utf-8"),
                    private=True,
                )
            bootstrap = (
                "import os,sys,runpy\nos.umask(0o077)\n"
                "from pathlib import Path\n"
                "os.environ['JARVIS_DATA_DIR']=str(Path(__file__).parent/'data')\n"
                "os.environ['JARVIS_CONFIG']=str(Path(__file__).parent/'jarvis.toml')\n"
                "import jarvis.society\n"
                "jarvis.society.__path__.insert(0,str(Path(__file__).parent/'modules'))\n"
                "runpy.run_module('jarvis.society.cloud_worker',run_name='__main__')\n"
            )
            for name, value in (
                ("bootstrap.py", bootstrap),
                ("access.token", token),
                ("manifest.json", json.dumps(row)),
                ("bundle.json", json.dumps(bundle, ensure_ascii=False)),
            ):
                await remote_os.upload_text(opened, host, f"{root}/{name}", value, private=True)
            command = f"{shlex.quote(info['python'])} {shlex.quote(root + '/bootstrap.py')}"
            prepared = await remote_os.run_script(
                opened, host, f"{command} prepare {shlex.quote(root)}\n", timeout_s=120
            )
            if prepared.exit_status != 0:
                # Do not relay a traceback containing transferred context.
                raise CloudHostError(
                    "The server could not prepare the agent. "
                    "Check its installed CLI and subscription login."
                )
            launcher = (
                f"{command} serve {shlex.quote(root)} >>{shlex.quote(root + '/host.log')} 2>&1"
            )
            session_name = shlex.quote("jarvis-cloud-" + row["transfer_id"])
            start = f"tmux new-session -d -s {session_name} {shlex.quote(launcher)}\n"
            launched = await remote_os.run_script(opened, host, start, timeout_s=30)
            if launched.exit_status != 0:
                raise CloudHostError("The server could not start its independent agent host.")
        deadline = asyncio.get_running_loop().time() + 120
        async with self.tunnel(row["agent_id"]) as (url, token):
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await _HTTP.client().get(
                        url + "/cloud/status",
                        headers={"Authorization": "Bearer " + token},
                    )
                    status = response.json()
                    if response.status_code == 200 and status.get("ready"):
                        return
                except Exception:
                    log.debug("Cloud host is still starting", exc_info=True)
                await asyncio.sleep(1 + secrets.randbelow(500) / 1000)
        raise CloudHostError("The server did not become ready; the local agent remains available.")
