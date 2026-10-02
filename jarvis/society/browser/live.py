"""Parent side of the managed browser protocol and shared live viewers."""

from __future__ import annotations

import asyncio
import contextvars
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
from .profiles import BrowserProfiles, ProfileBinding

log = logging.getLogger(__name__)
RPC = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
MAX_LINE = 8 * 1024 * 1024
_FORCED_CLOSE_TIMEOUT_S = 2.0
# One login owner can use 2 s to acknowledge shutdown, 12 s to flush Chrome
# and release native capture, then 2 s to reap. Parallel owners share a budget.
_SESSIONS_CLOSE_TIMEOUT_S = 17.0
_BUSY = "This browser is busy or under manual control"
_PAUSED = (
    "This browser is paused until you allow the action in the browser panel. "
    "It is still working. Leave it running and call society_browser again after "
    "it is allowed. Do not cancel the browser over HTTP."
)


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
            kind = str(event.get("kind"))
            if (
                kind == "state"
                and self.frame
                and event.get("generation") != self.frame.get("generation")
            ):
                self.frame = None
            # Coalescing must preserve last-occurrence order: an old clear event
            # must reach a slow viewer before a newer approval of the same kind.
            self.metadata.pop(kind, None)
            self.metadata[kind] = event
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
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: dict[str, Any] = field(default_factory=dict)
    rpc: dict[str, RPC] = field(default_factory=dict)
    rpc_context: contextvars.Context | None = None
    control_owner: str | None = None
    manual_epoch: str = ""
    active_trace: str = ""
    active_chat: str = ""
    generation: str = ""
    closed: bool = False
    stderr_tail: str = ""
    window_upgrade_pending: bool = False
    profile_binding: ProfileBinding | None = None
    profile_lease: Any = None
    login_guard: bool = False

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
            # The owning run already sends cancellation; a stale RPC must not reply.
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
                    task = asyncio.create_task(
                        self.answer_rpc(event),
                        context=self.rpc_context.copy() if self.rpc_context is not None else None,
                    )
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif kind == "state":
                    if self.login_guard:
                        event = {**event, "manual": True, "login_mode": True}
                    self.state = event
                    self.publish(event)
                elif kind in {"frame", "pointer", "dialog", "download", "warning", "step"}:
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

    async def watch_exit(self) -> None:
        """Reap descendants even if they keep the dead worker's pipes open.

        asyncio Process.wait() also waits for pipe EOF, so it cannot be the
        crash detector here. returncode is updated by the OS process watcher
        independently of inherited pipe handles.
        """
        while self.proc.returncode is None:  # noqa: ASYNC110 — wait() depends on inherited pipe EOF
            await asyncio.sleep(0.1)
        self.tree.close()

    async def close(self) -> None:
        try:
            if not self.closed:
                try:
                    await self.command("shutdown", timeout=2)
                except Exception:
                    log.debug("Browser graceful shutdown unavailable", exc_info=True)
            if self.proc.stdin:
                self.proc.stdin.close()
            for task in list(self.tasks):
                task.cancel()
            graceful_timeout = 12 if self.login_guard or self.state.get("login_mode") else 5
            await asyncio.wait_for(self.proc.wait(), timeout=graceful_timeout)
        except TimeoutError:
            log.debug("Browser graceful shutdown timed out; closing its process tree")
        finally:
            # Cancellation can arrive during either graceful wait. Release the
            # containment handle and terminate the owned worker before awaiting
            # anything else, then bound the process/task joins independently.
            self.closed = True
            try:
                self.tree.close()
            finally:
                if self.proc.returncode is None:
                    try:
                        self.proc.kill()
                    except ProcessLookupError:
                        pass  # The containment close already reaped this worker.
                    except OSError as exc:
                        log.warning("Browser worker termination failed: %s", type(exc).__name__)
                if self.proc.stdin:
                    self.proc.stdin.close()
                tasks = set(self.tasks) | set(self.readers)
                for task in tasks:
                    task.cancel()
                reaping = asyncio.create_task(self.proc.wait())
                done, pending = await asyncio.wait(
                    tasks | {reaping}, timeout=_FORCED_CLOSE_TIMEOUT_S
                )
                for task in done:
                    if not task.cancelled() and (error := task.exception()) is not None:
                        log.warning("Browser cleanup task failed: %s", type(error).__name__)
                if pending:
                    # Keep reader/RPC handles available for a later close retry.
                    reaping.cancel()
                    raise TimeoutError("browser process cleanup incomplete")
                if self.profile_lease is not None:
                    self.profile_lease.release()
                    self.profile_lease = None


async def claim_browser(session: LiveSession, chat_session_id: str) -> None:
    """Reserve the browser, replacing this chat's own unfinished run.

    A client that stops waiting (the three-minute tool deadline) leaves the
    run holding the lock. The same chat's next call is that abandoned run,
    so it takes over. A person at the controls, another chat, or a run that
    is paused for approval stays as it is.
    """
    if session.control_owner:
        raise RuntimeError(_BUSY)
    if not session.run_lock.locked():
        return
    same_chat = bool(chat_session_id) and session.active_chat == chat_session_id
    if same_chat and "approval" in session.attention:
        raise RuntimeError(_PAUSED)
    if same_chat:
        await _replace_owned_run(session)
        return
    if not session.active_chat:
        # The owner is already leaving; wait out the hand-off instead of
        # telling the caller the browser is busy.
        try:
            async with asyncio.timeout(5):
                async with session.run_lock:
                    pass
        except TimeoutError:
            raise RuntimeError(_BUSY) from None
        return
    raise RuntimeError(_BUSY)


async def _replace_owned_run(session: LiveSession) -> None:
    if not session.closed:
        try:
            await session.command("cancel", timeout=5)
        except Exception:
            log.warning("Could not stop the chat's previous browser task", exc_info=True)
    try:
        async with asyncio.timeout(15):
            async with session.run_lock:
                pass
    except TimeoutError:
        raise RuntimeError(_BUSY) from None


class LiveSessions:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.sessions: dict[str, LiveSession] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.model_resolver: Callable[[Any], Any] | None = None
        self.executor: Any = None
        self.cdp_url = "http://127.0.0.1:9222"
        self.idle_tasks: dict[str, asyncio.Task] = {}
        self._closing_tasks: dict[str, asyncio.Task[None]] = {}
        self.stopped_turns: dict[tuple[str, str], None] = {}
        self.profiles = BrowserProfiles(data_dir)
        from .chrome import ChromeConnections

        self.chrome = ChromeConnections()
        self.profile_start_lock = asyncio.Lock()

    def stop_turn(self, agent_id: str, trace_id: str) -> None:
        if trace_id:
            self.stopped_turns[(agent_id, trace_id)] = None
            while len(self.stopped_turns) > 512:
                self.stopped_turns.pop(next(iter(self.stopped_turns)))

    async def cancel(self, session: LiveSession, *, end_turn: bool = True) -> dict:
        # end_turn is the viewer's Stop button. A bare cancel only releases the
        # browser, so the agent can keep working after its own recovery call.
        if end_turn:
            self.stop_turn(session.agent_id, session.active_trace)
        if session.closed:
            return {}
        return await session.command("cancel")

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
                    and not session.state.get("manual", False)
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

    async def ensure(self, agent: Any, *, window_view: bool = False) -> LiveSession:
        """Select the saved identity before opening any browser or reusing a tab."""
        async with self.profile_start_lock:
            binding = await asyncio.to_thread(self.profiles.resolve, agent)
            profile_key = "legacy-attach" if binding.kind == "attach" else binding.key
            for other_id, other in list(self.sessions.items()):
                previous = getattr(other, "profile_binding", None)
                if previous is None:
                    continue
                same_profile = previous.key == binding.key or (
                    previous.kind == binding.kind == "attach"
                )
                changed = other_id == agent.agent_id and previous.access_key != binding.access_key
                if changed or (same_profile and other_id != agent.agent_id):
                    if (
                        other.run_lock.locked()
                        or other.control_owner
                        or other.subscribers
                        or other.state.get("manual", False)
                    ):
                        raise RuntimeError(
                            "This profile is in use. Stop its task or return manual control first."
                        )
                    # The active panel follows the newly assigned owner. It must
                    # never keep accepting input into the old account.
                    other.publish({"kind": "disconnected"})
                    await other.close()
                    self.sessions.pop(other_id, None)
            old = self.sessions.get(agent.agent_id)
            if (
                old
                and not old.closed
                and old.profile_binding is not None
                and old.profile_binding.access_key == binding.access_key
            ):
                old.profile_agent = agent
                old.profile_binding = binding
                if binding.kind == "chrome":
                    return old
                lease = old.profile_lease
                old.profile_lease = None
                try:
                    session = await self._ensure_managed(agent, binding, window_view=window_view)
                    session.profile_binding = binding
                    session.profile_lease = lease
                    return session
                except BaseException:
                    if lease is not None:
                        lease.release()
                    raise
            if old:
                await old.close()
                self.sessions.pop(agent.agent_id, None)
            from filelock import FileLock, Timeout

            lock_dir = self.data_dir / "society" / "browser-locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            lease = FileLock(str(lock_dir / f"{profile_key}.lock"), thread_local=False)
            try:
                lease.acquire(timeout=0)
            except Timeout:
                raise RuntimeError(
                    "This browser profile is already open in another Jarvis instance"
                ) from None
            try:
                if binding.kind == "chrome":
                    from .extension_session import create_chrome_session

                    session = await create_chrome_session(
                        self.chrome,
                        profile_id=binding.profile_id,
                        agent_id=agent.agent_id,
                        allowed_domains=list(binding.domains),
                        workspace=self.data_dir / "society" / agent.agent_id / "workspace",
                    )
                    self.sessions[agent.agent_id] = session
                else:
                    session = await self._ensure_managed(agent, binding, window_view=window_view)
                session.profile_binding = binding
                session.profile_agent = agent
                session.profile_lease = lease
                self.release_when_idle(session)
                return session
            except BaseException:
                lease.release()
                raise

    async def invalidate_profiles(self, agent_ids: set[str] | None = None) -> None:
        """Disconnect stale viewers; a settings change never reuses the old identity."""
        async with self.profile_start_lock:
            await self._invalidate_profiles(agent_ids)

    async def configure_profiles(
        self,
        fn: Any,
        *args: Any,
        agent_ids: set[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Serialize durable reassignment with session creation and invalidate old owners."""
        async with self.profile_start_lock:
            result = await asyncio.to_thread(fn, *args, **kwargs)
            await self._invalidate_profiles(agent_ids, changed_only=True)
            return result

    async def _invalidate_profiles(
        self,
        agent_ids: set[str] | None,
        *,
        changed_only: bool = False,
    ) -> None:
        for aid, session in list(self.sessions.items()):
            if agent_ids is not None and aid not in agent_ids:
                continue
            prior = getattr(session, "profile_binding", None)
            agent = getattr(session, "profile_agent", None)
            if changed_only and prior is not None and agent is not None:
                try:
                    current = await asyncio.to_thread(self.profiles.resolve, agent)
                except ValueError:
                    current = None  # Revoked identities must invalidate the old session.
                if current is not None and current.access_key == prior.access_key:
                    session.profile_binding = current
                    continue
            self.stop_turn(aid, session.active_trace)
            session.publish({"kind": "disconnected"})
            await session.close()
            self.sessions.pop(aid, None)

    async def _ensure_managed(
        self,
        agent: Any,
        binding: ProfileBinding,
        *,
        window_view: bool = False,
    ) -> LiveSession:
        agent_id = agent.agent_id
        idle = self.idle_tasks.pop(agent_id, None)
        if idle:
            idle.cancel()
        async with self.locks.setdefault(agent_id, asyncio.Lock()):
            old = self.sessions.get(agent_id)
            if old and not old.closed:
                upgrade = (
                    window_view
                    and os.name == "nt"
                    and getattr(agent, "browser_mode", "own") == "own"
                    and not old.state.get("full_window", False)
                )
                if not upgrade:
                    return old
                if (
                    old.run_lock.locked()
                    or old.control_owner
                    or getattr(old, "login_guard", False)
                    or old.state.get("manual")
                    or old.state.get("login_mode")
                ):
                    old.window_upgrade_pending = True
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
                asyncio.create_task(session.watch_exit()),
            ]
            try:
                result = await session.command(
                    "ensure",
                    {
                        "profile_dir": str(binding.path),
                        "window_view": window_view,
                        "workspace": str(folder / "workspace"),
                        "executable": str(install.browser_executable(self.data_dir)),
                        "creationflags": NO_WINDOW_CREATIONFLAGS,
                        "icon_path": str(
                            Path(__file__).parents[2] / "assets" / "icons" / "jarvis.ico"
                        ),
                        "allowed_domains": list(binding.domains),
                        "cdp_url": self.cdp_url if binding.kind == "attach" else "",
                    },
                    timeout=90,
                )
                session.generation = result["generation"]
                session.login_guard = bool(result.get("login_mode"))
                session.state = {
                    "kind": "state",
                    "generation": session.generation,
                    "running": False,
                    "url": "",
                    "target": "",
                    "tabs": [],
                    **session.state,
                    "manual": bool(result.get("manual")),
                    "full_window": bool(result.get("full_window")),
                    "login_available": bool(result.get("login_available")),
                    "login_mode": bool(result.get("login_mode")),
                    "login_ready": bool(result.get("login_ready")),
                }
                self.sessions[agent_id] = session
                self.release_when_idle(session)
                return session
            except BaseException:
                await session.close()
                raise

    async def subscribe(self, agent: Any) -> tuple[LiveSession, LiveUpdates]:
        session = await self.ensure(agent, window_view=True)
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
                # A lost viewer is not permission to resume observing a real
                # Chrome login. Keep it paused until a person explicitly returns.
                binding = getattr(session, "profile_binding", None)
                if not session.state.get("login_mode") and (
                    binding is None or binding.kind != "chrome"
                ):
                    await session.command("takeover", {"enabled": False})
                session.control_owner = None
                if session.window_upgrade_pending:
                    session.publish({"kind": "disconnected"})
            if not session.subscribers:
                await session.command("subscribe", {"enabled": False})
                self.release_when_idle(session)

    async def control(
        self,
        session: LiveSession,
        owner: str,
        op: str,
        args: dict,
        *,
        expected_manual: tuple[str, str] | None = None,
    ) -> dict:
        if op == "takeover":
            async with session.control_lock:
                if expected_manual is not None and (
                    (session.manual_epoch, session.generation) != expected_manual
                    or not (session.state.get("manual") or session.state.get("login_mode"))
                ):
                    raise ValueError("This login session has already ended")
                if session.control_owner not in {None, owner}:
                    raise ValueError("Browser is controlled by another viewer")
                if args.get("enabled") and "approval" in session.attention:
                    raise ValueError("Resolve the pending approval or stop the task first")
                login_requested = args.get("login") is True
                if login_requested and not session.state.get("login_available"):
                    raise ValueError("In-window Chrome sign-in is unavailable in this session")
                keep_paused = login_requested or bool(session.state.get("login_mode"))
                session.publish({"kind": "control_pending"})
                try:
                    if login_requested:
                        # Reserve the profile before cancelling a task. Neither a
                        # concurrent run nor viewer loss may reconnect automation.
                        session.control_owner = owner
                        session.login_guard = True
                        session.state.update(manual=True, login_mode=True)
                        await self.cancel(session)
                    result = await session.command(op, args, timeout=610)
                except BaseException:
                    if keep_paused:
                        session.login_guard = True
                        session.state.update(manual=True, login_mode=True)
                        session.publish(
                            {
                                "kind": "control",
                                "ok": False,
                                "manual": True,
                                "login_mode": True,
                                "error": "Sign-in remains paused. Retry in the browser panel.",
                            }
                        )
                        raise
                    try:
                        binding = getattr(session, "profile_binding", None)
                        if not session.closed and (binding is None or binding.kind != "chrome"):
                            await session.command("takeover", {"enabled": False}, timeout=5)
                    finally:
                        session.control_owner = None
                        session.publish({"kind": "control", "ok": True, "manual": False})
                    raise
                session.control_owner = owner if args.get("enabled") else None
                if args.get("enabled") is False and result.get("login_mode") is False:
                    session.login_guard = False
                session.state.update(
                    manual=result.get("manual", bool(args.get("enabled"))),
                    login_mode=result.get("login_mode", session.state.get("login_mode", False)),
                )
                generation = result.get("generation")
                if isinstance(generation, str) and generation:
                    session.generation = generation
                if session.state.get("manual") or session.state.get("login_mode"):
                    if not getattr(session, "manual_epoch", ""):
                        session.manual_epoch = uuid4().hex
                else:
                    # A viewer reclaim stays in the same cycle. Successful
                    # handback revokes API completion rights for that cycle.
                    session.manual_epoch = ""
                if not session.control_owner and session.window_upgrade_pending:
                    session.publish({"kind": "disconnected"})
                session.publish({"kind": "control", "ok": True, **result})
                return result
        if op != "cancel" and session.control_owner != owner:
            raise ValueError("Take browser control first")
        if op == "cancel":
            return await self.cancel(session)
        if session.state.get("login_available") and args.get("generation") != session.state.get(
            "generation"
        ):
            raise ValueError("The browser changed; wait for its new image")
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
        trace_id: str = "",
        chat_session_id: str = "",
    ) -> dict:
        session = await self.ensure(agent)
        await claim_browser(session, chat_session_id)
        if (
            session.run_lock.locked()
            or session.control_owner
            or session.state.get("manual")
            or session.state.get("login_mode")
        ):
            raise RuntimeError(_BUSY)
        async with session.run_lock:
            session.rpc = {"llm": llm, "action": action}
            session.rpc_context = contextvars.copy_context()
            session.active_trace = trace_id
            session.active_chat = chat_session_id
            try:
                result = await session.command(
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
                if result.get("uncertain"):
                    self.stop_turn(agent.agent_id, trace_id)
                return result
            except BaseException:
                binding = getattr(session, "profile_binding", None)
                if (
                    binding is not None
                    and binding.kind == "chrome"
                    and (session.closed or getattr(session, "action_uncertain", False))
                ):
                    self.stop_turn(agent.agent_id, trace_id)
                if not session.closed:
                    await session.command("cancel", timeout=5)
                raise
            finally:
                for pending in list(session.tasks):
                    pending.cancel()
                await asyncio.gather(*session.tasks, return_exceptions=True)
                session.rpc = {}
                session.rpc_context = None
                session.active_trace = ""
                session.active_chat = ""
                if session.window_upgrade_pending:
                    session.publish({"kind": "disconnected"})
                if not session.subscribers:
                    self.release_when_idle(session)

    async def close(self) -> None:
        idle = list(self.idle_tasks.values())
        for task in idle:
            task.cancel()
        sessions = list(self.sessions.items())
        closing = []
        for key, session in sessions:
            task = self._closing_tasks.get(key)
            if task is None or task.done():
                task = asyncio.create_task(session.close())
                self._closing_tasks[key] = task
            closing.append(task)
        owned = set(idle) | set(closing)
        try:
            if owned:
                # Unlike gather, wait returns immediately on caller cancellation,
                # so a reluctant idle/RPC task cannot defeat the server deadline.
                _done, pending = await asyncio.wait(owned, timeout=_SESSIONS_CLOSE_TIMEOUT_S)
                if pending:
                    raise TimeoutError("browser sessions shutdown incomplete")
            for task in closing:
                task.result()
        finally:
            pending = {task for task in owned if not task.done()}
            for task in pending:
                task.cancel()
            if pending:
                _done, pending = await asyncio.wait(pending, timeout=3)
            for task in owned:
                if task.done() and not task.cancelled() and task.exception() is not None:
                    log.warning("Browser owner cleanup failed: %s", type(task.exception()).__name__)
            for key, task in list(self.idle_tasks.items()):
                if task.done():
                    self.idle_tasks.pop(key, None)
            for (key, session), task in zip(sessions, closing, strict=True):
                if task.done():
                    self._closing_tasks.pop(key, None)
                if (
                    task.done()
                    and (task.cancelled() or task.exception() is None)
                    and (not hasattr(session, "proc") or session.proc.returncode is not None)
                    and all(job.done() for job in (*session.tasks, *session.readers))
                ):
                    self.sessions.pop(key, None)
            if pending:
                raise TimeoutError("browser sessions cleanup incomplete")
            await self.chrome.close()
