"""Parent side of the managed browser protocol and shared live viewers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.core.process_tree import ProcessTree, make_process_tree
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from . import install

log = logging.getLogger(__name__)
RPC = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
MAX_LINE = 8 * 1024 * 1024


class LiveUpdates:
    """Latest pixels plus coalesced control events; a frame cannot drop approval."""

    def __init__(self) -> None:
        self.frame: dict | None = None
        self.metadata: dict[str, dict] = {}
        self.changed = asyncio.Event()

    def put_nowait(self, event: dict) -> None:
        if event.get("kind") == "frame":
            self.frame = event
        else:
            self.metadata[str(event.get("kind"))] = event
        self.changed.set()

    async def get(self) -> dict:
        while True:
            if self.metadata:
                return self.metadata.pop(next(iter(self.metadata)))
            if self.frame is not None:
                frame, self.frame = self.frame, None
                return frame
            self.changed.clear()
            await self.changed.wait()


@dataclass
class LiveSession:
    agent_id: str
    proc: asyncio.subprocess.Process
    tree: ProcessTree
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    action_results: dict[str, asyncio.Future] = field(default_factory=dict)
    subscribers: set[LiveUpdates] = field(default_factory=set)
    attention: dict[str, dict] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    readers: list[asyncio.Task] = field(default_factory=list)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: dict[str, Any] = field(default_factory=dict)
    rpc: dict[str, RPC] = field(default_factory=dict)
    control_owner: str | None = None
    generation: str = ""
    closed: bool = False
    stderr_tail: str = ""

    async def send(self, value: dict[str, Any]) -> None:
        data = (json.dumps(value, ensure_ascii=True) + "\n").encode()
        if len(data) > MAX_LINE:
            raise ValueError("Browser request is too large")
        async with self.write_lock:
            if self.closed or self.proc.stdin is None:
                raise RuntimeError("Browser session disconnected")
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def command(self, op: str, args: dict | None = None, timeout: float = 60) -> dict:  # noqa: ASYNC109 — protocol request deadline
        key = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({"kind": "command", "id": key, "op": op, "args": args or {}})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(key, None)

    def publish(self, event: dict) -> None:
        kind = event.get("kind")
        if kind in {"approval", "dialog"}:
            self.attention[kind] = event
        elif kind in {"approval_cleared", "dialog_cleared"}:
            self.attention.pop(kind.removesuffix("_cleared"), None)
        for queue in self.subscribers:
            queue.put_nowait(event)

    async def answer_rpc(self, event: dict) -> None:
        callback = self.rpc.get(event["kind"])
        applied = False

        async def apply_action() -> dict:
            nonlocal applied
            applied = True
            key = event["id"]
            future = asyncio.get_running_loop().create_future()
            self.action_results[key] = future
            try:
                await self.send({"kind": "rpc_result", "id": key, "ok": True, "permit": key})
                return await asyncio.wait_for(future, 90)
            finally:
                self.action_results.pop(key, None)

        try:
            if event["kind"] == "action":
                event["payload"]["apply"] = apply_action
            answer = (
                await callback(event["payload"])
                if callback
                else {"ok": False, "error": "No active browser task"}
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.warning("Browser RPC failed: %s", type(exc).__name__, exc_info=True)
            answer = {"ok": False, "error": str(exc)[:1000]}
        if not self.closed and not applied:
            await self.send({"kind": "rpc_result", "id": event["id"], **answer})

    async def read(self) -> None:
        assert self.proc.stdout is not None
        try:
            while raw := await self.proc.stdout.readline():
                event = json.loads(raw)
                kind = event.get("kind")
                if kind == "response":
                    future = self.pending.get(event.get("id"))
                    if future and not future.done():
                        if event.get("ok"):
                            future.set_result(event.get("result") or {})
                        else:
                            future.set_exception(RuntimeError(event.get("error", "Browser failed")))
                elif kind == "action_result":
                    future = self.action_results.get(event.get("id"))
                    if future and not future.done():
                        future.set_result(event.get("result") or {})
                elif kind in {"llm", "action"}:
                    task = asyncio.create_task(self.answer_rpc(event))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif kind == "state":
                    self.state = event
                    self.publish(event)
                elif kind in {"frame", "dialog", "download", "warning", "step"}:
                    self.publish(event)
                elif kind == "fatal":
                    raise RuntimeError(event.get("error", "Browser worker failed"))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Browser protocol ended for %s", self.agent_id, exc_info=True)
        finally:
            self.closed = True
            for future in (*self.pending.values(), *self.action_results.values()):
                if not future.done():
                    future.set_exception(RuntimeError("Browser session disconnected"))
            self.publish({"kind": "disconnected"})
            self.tree.close()

    async def drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(8192):
            self.stderr_tail = (self.stderr_tail + chunk.decode("utf-8", "replace"))[-4000:]

    async def close(self) -> None:
        if not self.closed:
            try:
                await self.command("shutdown", timeout=2)
            except Exception:
                log.debug("Browser graceful shutdown unavailable", exc_info=True)
        if self.proc.stdin:
            self.proc.stdin.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except TimeoutError:
            self.tree.close()
            if self.proc.returncode is None:
                self.proc.kill()
            await self.proc.wait()
        self.tree.close()
        for task in self.readers:
            task.cancel()
        await asyncio.gather(*self.readers, return_exceptions=True)
        self.closed = True


class LiveSessions:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.sessions: dict[str, LiveSession] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.model_resolver: Callable[[Any], Any] | None = None
        self.executor: Any = None
        self.cdp_url = "http://127.0.0.1:9222"
        self.idle_tasks: dict[str, asyncio.Task] = {}

    def release_when_idle(self, session: LiveSession) -> None:
        old = self.idle_tasks.pop(session.agent_id, None)
        if old:
            old.cancel()

        async def expire() -> None:
            await asyncio.sleep(300)
            async with self.locks.setdefault(session.agent_id, asyncio.Lock()):
                if (
                    not session.subscribers
                    and not session.run_lock.locked()
                    and not session.control_owner
                ):
                    if self.sessions.get(session.agent_id) is session:
                        self.sessions.pop(session.agent_id, None)
                        closing = asyncio.create_task(session.close())
                        try:
                            await asyncio.shield(closing)
                        except asyncio.CancelledError:
                            await closing
                            raise
            self.idle_tasks.pop(session.agent_id, None)

        self.idle_tasks[session.agent_id] = asyncio.create_task(expire())

    async def ensure(self, agent: Any) -> LiveSession:
        agent_id = agent.agent_id
        idle = self.idle_tasks.pop(agent_id, None)
        if idle:
            idle.cancel()
        async with self.locks.setdefault(agent_id, asyncio.Lock()):
            old = self.sessions.get(agent_id)
            if old and not old.closed:
                return old
            if old:
                await old.close()
            if not install.is_installed(self.data_dir):
                await asyncio.to_thread(install.ensure_installed, self.data_dir)
            folder = (self.data_dir / "society" / agent_id).resolve()
            tree = make_process_tree("agent-browser")
            process_options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
            proc = await asyncio.create_subprocess_exec(
                str(install.venv_python(self.data_dir)),
                str(install.runner_path()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE,
                env=install.worker_env(self.data_dir),
                creationflags=NO_WINDOW_CREATIONFLAGS,
                **process_options,
            )
            tree.assign(proc.pid)
            session = LiveSession(agent_id, proc, tree)
            session.readers = [
                asyncio.create_task(session.read()),
                asyncio.create_task(session.drain_stderr()),
            ]
            try:
                result = await session.command(
                    "ensure",
                    {
                        "profile_dir": str(folder / "browser-profile"),
                        "workspace": str(folder / "workspace"),
                        "executable": str(install.browser_executable(self.data_dir)),
                        "allowed_domains": list(agent.browser_allowed_domains),
                        "cdp_url": self.cdp_url
                        if str(getattr(agent, "browser_mode", "own")) == "attach"
                        else "",
                    },
                    timeout=90,
                )
                session.generation = result["generation"]
                self.sessions[agent_id] = session
                self.release_when_idle(session)
                return session
            except BaseException:
                await session.close()
                raise

    async def subscribe(self, agent: Any) -> tuple[LiveSession, LiveUpdates]:
        session = await self.ensure(agent)
        queue = LiveUpdates()
        session.subscribers.add(queue)
        try:
            await session.command("subscribe", {"enabled": True})
        except BaseException:
            session.subscribers.discard(queue)
            raise
        if session.state:
            queue.put_nowait(session.state)
        for event in session.attention.values():
            queue.put_nowait(event)
        return session, queue

    async def unsubscribe(self, session: LiveSession, queue: LiveUpdates, owner: str) -> None:
        session.subscribers.discard(queue)
        if not session.closed:
            if session.control_owner == owner:
                await session.command("takeover", {"enabled": False})
                session.control_owner = None
            if not session.subscribers:
                await session.command("subscribe", {"enabled": False})
                self.release_when_idle(session)

    async def control(self, session: LiveSession, owner: str, op: str, args: dict) -> dict:
        if op == "takeover":
            if session.control_owner not in {None, owner}:
                raise ValueError("Browser is controlled by another viewer")
            result = await session.command(op, args)
            session.control_owner = owner if args.get("enabled") else None
            return result
        if op != "cancel" and session.control_owner != owner:
            raise ValueError("Take browser control first")
        result = await session.command(op, args)
        if op == "dialog":
            session.publish({"kind": "dialog_cleared"})
        return result

    async def run(
        self,
        agent: Any,
        *,
        task: str,
        max_steps: int,
        llm: RPC,
        action: RPC,
        vision: bool = True,
        files: list[str] | None = None,
    ) -> dict:
        session = await self.ensure(agent)
        if session.run_lock.locked() or session.control_owner:
            raise RuntimeError("This browser is busy or under manual control")
        async with session.run_lock:
            session.rpc = {"llm": llm, "action": action}
            try:
                return await session.command(
                    "run",
                    {
                        "task": task,
                        "max_steps": max_steps,
                        "model": agent.model,
                        "vision": vision,
                        "files": files or [],
                    },
                    timeout=600,
                )
            except BaseException:
                if not session.closed:
                    await session.command("cancel", timeout=5)
                raise
            finally:
                for pending in list(session.tasks):
                    pending.cancel()
                await asyncio.gather(*session.tasks, return_exceptions=True)
                session.rpc = {}
                if not session.subscribers:
                    self.release_when_idle(session)

    async def close(self) -> None:
        for task in self.idle_tasks.values():
            task.cancel()
        await asyncio.gather(*self.idle_tasks.values(), return_exceptions=True)
        self.idle_tasks.clear()
        await asyncio.gather(*(s.close() for s in self.sessions.values()), return_exceptions=True)
        self.sessions.clear()
