"""Native goal adapters. Their processes never share the realtime voice transport."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


def disable_cli_tools(plan: Any, runner: str) -> None:
    """A verifier has no tools, including user-installed MCP servers."""
    argv: list[str] = []
    it = iter(plan.argv)
    for part in it:
        if part in ("--mcp-config", "--append-system-prompt", "--append-system-prompt-file"):
            next(it, None)
        elif part == "-c":
            value = next(it)
            if not value.startswith("mcp_servers."):
                argv.extend((part, value))
        else:
            argv.append(part)
    if runner in ("claude-cli", "glm-cli"):
        argv += ["--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if "--resume" not in argv:
            argv.append("--no-session-persistence")
    elif runner == "codex-cli":
        # exec ignores only configuration, not the selected account's auth store.
        argv += ["--ignore-user-config", "--ignore-rules"]
        if "resume" not in argv:
            argv.append("--ephemeral")
        for feature in (
            "shell_tool",
            "apps",
            "hooks",
            "multi_agent",
            "browser_use",
            "web_search_request",
            "goals",
            "memories",
            "plugins",
            "computer_use",
            "image_generation",
            "multi_agent_v2",
        ):
            argv += ["--disable", feature]
    elif runner in ("agy-cli", "grok-cli", "opencode-cli", "cursor-cli"):
        # These runners expose native plan mode, but not a tool-free print option.
        flag = {
            "agy-cli": "--mode",
            "grok-cli": "--permission-mode",
            "opencode-cli": "--agent",
            "cursor-cli": "--mode",
        }[runner]
        if flag not in argv or argv[argv.index(flag) + 1] != "plan":
            raise ValueError("The verifier requires native read-only mode")
    else:
        raise ValueError("No tool-free mode for this runner")
    plan.argv[:] = argv


def native_adapters() -> list[Any]:
    return [NativeCodexGoal(), NativeClaudeGoal()]


class NativeClaudeGoal:
    name = "claude-native"
    cleanup_done = False

    async def available(self, session: Any) -> bool:
        from .runner_cli import ACCOUNT_OVERRIDE, _account_env, claude_argv_prefix
        from .service import resolve_runner

        if resolve_runner(session.provider, surface=session.surface) not in (
            "claude-cli",
            "glm-cli",
        ):
            return False
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [*claude_argv_prefix(), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
            supported = (
                result.returncode == 0
                and version is not None
                and tuple(map(int, version.groups())) >= (2, 1, 239)
            )
            if not supported:
                return False
            token = ACCOUNT_OVERRIDE.set(session.account_id or "")
            try:
                env = _account_env("claude")
            finally:
                ACCOUNT_OVERRIDE.reset(token)
            probe = await asyncio.to_thread(
                subprocess.run,
                [
                    *claude_argv_prefix(),
                    "--print",
                    "/goal",
                    "--output-format",
                    "json",
                    "--no-session-persistence",
                    "--tools",
                    "",
                    "--strict-mcp-config",
                    "--mcp-config",
                    '{"mcpServers":{}}',
                ],
                cwd=session.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                response = json.loads(probe.stdout)
            except ValueError:
                return False
            if not isinstance(response, dict):
                return False
            return (
                probe.returncode == 0
                and not response.get("is_error")
                and "no goal" in str(response.get("result", "")).casefold()
            )
        except (OSError, subprocess.SubprocessError):
            log.debug("Native Claude goal probe unavailable", exc_info=True)
            return False

    async def run_goal(self, service: Any, session: Any, objective: str) -> dict[str, Any]:
        self.cleanup_done = False
        from .control_eval import evaluate_goal

        events = service.store.list_events(session.session_id)
        after = events[-1]["seq"] if events else 0
        try:
            # Claude recognizes its native command at offset zero, including print mode.
            await service.controls._send(
                session.session_id,
                "/goal " + objective,
                direct_user=False,
                control_owned=True,
                native_goal=True,
            )
            await service.wait_turn(session.session_id)
            events = service.store.list_events(session.session_id, after_seq=after)
            finished = [e for e in events if e["kind"] == "turn_finished"]
            if not finished or finished[-1]["payload"].get("status") != "done":
                from jarvis.core.redact import redact_secrets

                reason = finished[-1]["payload"].get("error") if finished else None
                raise ValueError(
                    redact_secrets(str(reason or "Native goal failed or was interrupted"))
                )
            # A successful process exit is not a completion verdict. Verify actual evidence.
            verdict = await evaluate_goal(
                session,
                objective,
                events,
                service.controls.state(session.session_id).output_language,
            )
            return {
                "status": "complete" if verdict.status == "complete" else "blocked",
                "reason": verdict.reason,
                "evidence": verdict.evidence,
            }
        finally:
            await service.cancel(session.session_id)
            # A stopped native goal must not restart secretly on the next ordinary message.
            await self._clear(service, session.session_id)
            self.cleanup_done = True

    async def clear_saved(self, service: Any, session: Any) -> None:
        await self._clear(service, session.session_id)
        self.cleanup_done = True

    async def _clear(self, service: Any, sid: str) -> None:
        from dataclasses import replace

        from .runner_api import TurnHandle
        from .runner_cli import run_cli_turn
        from .service import resolve_runner

        session = service.store.get_session(sid)
        if not session or not session.vendor_session:
            return
        captured = []

        async def emit(event: Any) -> None:
            captured.append(event)

        async def deny(*_: Any) -> str:
            return "deny"

        handle = TurnHandle(
            session=replace(session, permission_mode="plan"),
            turn_id="goal-clear",
            emit=emit,
            request_approval=deny,
            cancel=asyncio.Event(),
            tools_disabled=True,
        )
        await run_cli_turn(
            handle, "/goal clear", resolve_runner(session.provider, surface=session.surface)
        )
        if any(
            e["kind"] == "turn_finished" and e["payload"].get("status") != "done" for e in captured
        ):
            raise ValueError(
                "Native goal could not be cleared; resume is blocked until the account is available"
            )

    async def interrupt(self, session_id: str) -> None:
        # The service's cancellation event interrupts the owned CLI turn; finally clears its goal.
        return None


class GoalRpc:
    """Small JSONL client with request correlation, terminal read errors and tree cleanup."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str, approve: Any) -> None:
        self.argv, self.env, self.cwd, self.approve = argv, env, cwd, approve
        self.notifications: asyncio.Queue = asyncio.Queue()
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0
        self.proc: Any = None
        self.tasks: list[asyncio.Task] = []
        self.tree: Any = None
        self.broken = False

    async def __aenter__(self) -> Any:
        from jarvis.core.process_tree import make_process_tree

        self.tree = make_process_tree("chat-native-goal")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                start_new_session=os.name != "nt",
                limit=4 * 1024 * 1024,
            )
            self.tree.assign(self.proc.pid)
            self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._stderr())]
        except BaseException:
            self.tree.close()
            if self.proc and self.proc.returncode is None:
                self.proc.kill()
                await self.proc.wait()
            raise
        return self

    async def send(self, value: dict) -> None:
        self.proc.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        if self.broken:
            raise ConnectionError("Native goal stream is closed")
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.send({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self.pending.pop(rid, None)

    async def _read(self) -> None:
        try:
            while raw := await self.proc.stdout.readline():
                value = json.loads(raw.decode("utf-8"))
                if "id" in value and "method" not in value:
                    future = self.pending.get(value["id"])
                    if future and not future.done():
                        if "error" in value:
                            from jarvis.core.redact import redact_secrets

                            future.set_exception(
                                ValueError(
                                    "Native goal RPC: "
                                    + redact_secrets(
                                        str(value["error"].get("message", "Request rejected"))[
                                            :1000
                                        ]
                                    )
                                )
                            )
                        else:
                            future.set_result(value.get("result") or {})
                elif "id" in value:
                    # Approval callbacks run outside the reader so an RPC can still finish.
                    task = asyncio.create_task(self._answer(value))
                    self.tasks.append(task)
                else:
                    await self.notifications.put(value)
            raise ConnectionError("Native goal process ended")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.broken = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Native goal stream closed"))
            await self.notifications.put(
                {"method": "transport/error", "params": {"message": str(exc)}}
            )

    async def _answer(self, value: dict) -> None:
        try:
            params = value.get("params") or {}
            method = value["method"]
            if method in (
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            ):
                choice = await self.approve(
                    str(value["id"]),
                    method,
                    params,
                    str(params.get("command") or params.get("reason") or "Native tool action"),
                )
                await self.send(
                    {
                        "id": value["id"],
                        "result": {
                            "decision": "accept"
                            if choice in ("allow", "allow_always")
                            else "decline"
                        },
                    }
                )
            else:
                await self.send(
                    {
                        "id": value["id"],
                        "error": {"code": -32601, "message": "Unsupported native request"},
                    }
                )
        except Exception:
            log.warning("Native goal approval response failed", exc_info=True)

    async def _stderr(self) -> None:
        while await self.proc.stderr.readline():
            # stderr can contain account details; it is drained, not copied into chat.
            pass

    async def __aexit__(self, *_: Any) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tree.close()
        if self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass  # Tree containment already reaped the process.
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=3)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()


class NativeCodexGoal:
    name = "codex-native"
    cleanup_done = False

    def __init__(self) -> None:
        self.active: dict[str, tuple[Any, str, str]] = {}

    async def available(self, session: Any) -> bool:
        from .runner_cli import codex_argv_prefix
        from .service import resolve_runner

        if resolve_runner(session.provider, surface=session.surface) != "codex-cli":
            return False
        try:
            prefix = codex_argv_prefix()
            return await asyncio.to_thread(self._probe, prefix)
        except (OSError, subprocess.SubprocessError):
            log.debug("Native Codex goal probe unavailable", exc_info=True)
            return False

    @staticmethod
    def _probe(prefix: list[str]) -> bool:
        with tempfile.TemporaryDirectory(prefix="jarvis-codex-schema-") as folder:
            result = subprocess.run(
                [*prefix, "app-server", "generate-json-schema", "--out", folder],
                capture_output=True,
                encoding="utf-8",
                text=True,
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if result.returncode:
                return False
            schemas = "\n".join(
                path.read_text(encoding="utf-8") for path in Path(folder).rglob("*.json")
            )
            return all(
                name in schemas
                for name in (
                    "thread/goal/set",
                    "thread/goal/get",
                    "thread/goal/clear",
                    "thread/goal/updated",
                )
            )

    async def run_goal(self, service: Any, session: Any, objective: str) -> dict[str, Any]:
        self.cleanup_done = False
        previous = service.store.list_events(session.session_id)
        after = previous[-1]["seq"] if previous else 0
        self.outcome = {
            "status": "blocked",
            "reason": "Native goal did not return a verdict",
            "evidence": [],
        }
        await service.controls._send(
            session.session_id,
            objective,
            direct_user=False,
            control_owned=True,
            control_runner=self._run,
        )
        await service.wait_turn(session.session_id)
        events = service.store.list_events(session.session_id, after_seq=after)
        if self.outcome["status"] == "complete":
            from .control_eval import evaluate_goal

            verdict = await evaluate_goal(
                session,
                objective,
                events,
                service.controls.state(session.session_id).output_language,
            )
            self.outcome = {
                "status": "complete" if verdict.status == "complete" else "blocked",
                "reason": verdict.reason,
                "evidence": verdict.evidence,
            }
        else:
            failed = [
                e for e in events if e["kind"] == "turn_finished" and e["payload"].get("error")
            ]
            if failed:
                self.outcome["reason"] = str(failed[-1]["payload"]["error"])
        return self.outcome

    async def _run(self, handle: Any, objective: str) -> str | None:
        started_at = time.monotonic()
        from .events import make_event
        from .jarvis_harness import build_identity, codex_config_args
        from .runner_cli import (
            ACCOUNT_OVERRIDE,
            _account_env,
            _CodexState,
            _surface_identity,
            codex_argv_prefix,
            translate_codex_line,
        )

        session = handle.session
        token = ACCOUNT_OVERRIDE.set(session.account_id or "")
        try:
            env = _account_env("codex")
        finally:
            ACCOUNT_OVERRIDE.reset(token)
        argv = [*codex_argv_prefix(), "app-server", *codex_config_args(session.session_id)]
        async with GoalRpc(argv, env, session.cwd, handle.request_approval) as rpc:
            await rpc.request(
                "initialize",
                {
                    "clientInfo": {"name": "jarvis-chat-goals", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await rpc.send({"method": "initialized"})
            params = {
                "cwd": session.cwd,
                "model": session.model or None,
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
                "developerInstructions": await _surface_identity(session),
            }
            if not session.vendor_session and handle.history:
                identity = await build_identity(
                    session_id=session.session_id,
                    turn_id=handle.turn_id,
                    user_text=objective,
                    history=handle.history,
                    resume=None,
                    with_file=False,
                    prompt_override=params["developerInstructions"],
                )
                params["developerInstructions"] = identity.text
            if handle.output_language:
                params["developerInstructions"] = (
                    (params["developerInstructions"] or "")
                    + "\nRespond in this language: "
                    + handle.output_language
                )
            if session.permission_mode in ("bypass", "full-access", "bypassPermissions"):
                params.update(approvalPolicy="never", sandbox="danger-full-access")
            if session.vendor_session:
                params["threadId"] = session.vendor_session
                started = await rpc.request("thread/resume", params)
            else:
                started = await rpc.request("thread/start", params)
            thread_id = started["thread"]["id"]
            if handle.control_service is not None:
                handle.control_service.store.update_session(
                    session.session_id, vendor_session=thread_id
                )
            state = _CodexState(turn_id=handle.turn_id, vendor_session=thread_id)
            native_turn = ""
            self.active[session.session_id] = (rpc, thread_id, native_turn)

            async def cancel() -> None:
                await handle.cancel.wait()
                await self.interrupt(session.session_id)

            watcher = asyncio.create_task(cancel())
            previous_digest = ""
            stalled = 0
            step_evidence: list[str] = []
            turn_active = True
            terminal_goal = False
            try:
                while not rpc.notifications.empty():
                    stale = rpc.notifications.get_nowait()
                    if stale.get("method") == "transport/error":
                        raise ConnectionError("Native goal transport closed during setup")
                await rpc.request(
                    "thread/goal/set",
                    {"threadId": thread_id, "objective": objective, "status": "active"},
                )
                turn = await rpc.request(
                    "turn/start",
                    {"threadId": thread_id, "input": [{"type": "text", "text": objective}]},
                )
                native_turn = turn["turn"]["id"]
                self.active[session.session_id] = (rpc, thread_id, native_turn)
                while not handle.cancel.is_set():
                    event = await rpc.notifications.get()
                    method, data = event.get("method"), event.get("params") or {}
                    if method == "transport/error":
                        raise ConnectionError(data.get("message"))
                    if data.get("threadId") not in (None, thread_id):
                        continue
                    if method == "turn/started":
                        turn_active = True
                        native_turn = data["turn"]["id"]
                        self.active[session.session_id] = (rpc, thread_id, native_turn)
                    elif method == "item/agentMessage/delta":
                        await handle.emit(
                            make_event(
                                "text_delta",
                                {
                                    "turn_id": handle.turn_id,
                                    "message_id": data.get("itemId", "native"),
                                    "text": data.get("delta", ""),
                                },
                            )
                        )
                    elif method in ("item/started", "item/completed"):
                        item = dict(data.get("item") or {})
                        for native_key, cli_key in (
                            ("aggregatedOutput", "aggregated_output"),
                            ("exitCode", "exit_code"),
                            ("durationMs", "duration_ms"),
                        ):
                            if native_key in item:
                                item[cli_key] = item[native_key]
                        item["type"] = {
                            "agentMessage": "agent_message",
                            "commandExecution": "command_execution",
                            "fileChange": "file_change",
                            "mcpToolCall": "mcp_tool_call",
                        }.get(str(item.get("type") or ""), item.get("type"))
                        for translated in translate_codex_line(
                            {"type": method.replace("/", "."), "item": item}, state
                        ):
                            if translated["kind"] in ("assistant_text", "tool_result"):
                                payload = translated.get("payload") or {}
                                step_evidence.append(
                                    str(payload.get("text") or payload.get("output") or "")
                                )
                            await handle.emit(translated)
                    if method in ("thread/goal/updated", "turn/completed"):
                        if method == "turn/completed":
                            turn_active = False
                        goal = (
                            data.get("goal")
                            if method == "thread/goal/updated"
                            else (
                                await rpc.request("thread/goal/get", {"threadId": thread_id})
                            ).get("goal")
                        )
                        if (
                            goal
                            and goal.get("objective") == objective
                            and goal.get("status")
                            in (
                                "complete",
                                "blocked",
                                "paused",
                                "budget_limited",
                                "usage_limited",
                                "budgetLimited",
                                "usageLimited",
                            )
                        ):
                            self.outcome = {
                                "status": "complete" if goal["status"] == "complete" else "blocked",
                                "reason": str(goal.get("reason") or goal["status"]),
                                "evidence": ["native:thread/goal/updated"],
                            }
                            terminal_goal = True
                        if terminal_goal and not turn_active:
                            break
                        if (
                            method == "turn/completed"
                            and goal is None
                            and not handle.cancel.is_set()
                        ):
                            self.outcome = {
                                "status": "blocked",
                                "reason": "Native goal was cleared without a completion verdict",
                                "evidence": [],
                            }
                            break
                        if method == "turn/completed":
                            digest = hashlib.sha256("\n".join(step_evidence).encode()).hexdigest()
                            stalled = (
                                stalled + 1 if digest == previous_digest or not step_evidence else 0
                            )
                            previous_digest, step_evidence = digest, []
                            control = (
                                handle.control_service.controls if handle.control_service else None
                            )
                            if control is not None:
                                snapshot = control.state(session.session_id)
                                if snapshot.goal and snapshot.goal.status == "active":
                                    snapshot.goal.steps += 1
                                    snapshot.goal.stalled_steps = stalled
                                    await control.publish(snapshot)
                            if stalled >= 3:
                                self.outcome = {
                                    "status": "blocked",
                                    "reason": (
                                        "Native goal repeated the same result "
                                        "without progress in three steps"
                                    ),
                                    "evidence": [],
                                }
                                break
                        if method == "turn/completed" and data.get("turn", {}).get("status") in (
                            "failed",
                            "interrupted",
                        ):
                            raise ValueError("Native goal turn failed or was interrupted")
                if self.outcome.get("status") == "complete":
                    await handle.emit(
                        make_event(
                            "notice",
                            {
                                "kind": "native_goal_verdict",
                                "text": "",
                                "goal_status": "complete",
                                "objective": objective,
                                "thread_id": thread_id,
                            },
                        )
                    )
                await handle.emit(
                    make_event(
                        "turn_finished",
                        {
                            "turn_id": handle.turn_id,
                            "status": "cancelled" if handle.cancel.is_set() else "done",
                            "usage": state.usage,
                            "duration_ms": int((time.monotonic() - started_at) * 1000),
                            "error": None,
                        },
                    )
                )
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                await rpc.request("thread/goal/clear", {"threadId": thread_id})
                self.cleanup_done = True
                self.active.pop(session.session_id, None)
            return thread_id

    async def clear_saved(self, service: Any, session: Any) -> None:
        from .runner_cli import ACCOUNT_OVERRIDE, _account_env, codex_argv_prefix

        if not session.vendor_session:
            self.cleanup_done = True
            return
        token = ACCOUNT_OVERRIDE.set(session.account_id or "")
        try:
            env = _account_env("codex")
        finally:
            ACCOUNT_OVERRIDE.reset(token)

        async def deny(*_: Any) -> str:
            return "deny"

        async with GoalRpc([*codex_argv_prefix(), "app-server"], env, session.cwd, deny) as rpc:
            await rpc.request(
                "initialize",
                {
                    "clientInfo": {"name": "jarvis-goal-recovery", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await rpc.send({"method": "initialized"})
            await rpc.request(
                "thread/resume",
                {
                    "threadId": session.vendor_session,
                    "cwd": session.cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "config": {"features": {"goals": False}},
                },
            )
            await rpc.request("thread/goal/clear", {"threadId": session.vendor_session})
        self.cleanup_done = True

    async def interrupt(self, session_id: str) -> None:
        if session_id in self.active:
            rpc, thread_id, turn_id = self.active[session_id]
            await rpc.request("thread/goal/clear", {"threadId": thread_id})
            if turn_id:
                await rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
