"""dispatch_to_harness-Tool: vom Brain aufrufbar, delegiert an einen Harness.

Der Brain kann via Tool-Call OpenClaw / Codex / Python-Script / MCP-Remote
spawnen. Output wird akkumuliert, auf `max_output_chars` getrimmt und als
`ToolResult.output` zurückgegeben — der Brain sieht's in der nächsten Turn
als `role="tool"`-Message und kann dann zusammenfassen.

Optional: `parallel_harnesses` — mehrere Harnesses gleichzeitig und
aggregiert zurückgeben.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, HarnessTask, ToolResult
from jarvis.harness.manager import HarnessManager

log = logging.getLogger(__name__)

_TIMEOUT_EXIT_CODE = 124


class DispatchToHarnessTool:
    name: str = "dispatch_to_harness"
    risk_tier: str = "monitor"
    # NOT an LLM-visible router tool (removed from ROUTER_TOOLS 2026-06-28). This
    # class powers the INTERNAL local-action / computer-use fast path, called
    # programmatically with a registered harness name. The description lists only
    # harnesses that actually exist as entry-points — there is no ``openclaw``
    # or ``codex`` harness (heavy sub-agent work is the spawn_worker tool).
    description: str = (
        "Internal: invoke a registered sub-agent harness "
        "(open-interpreter, python-script, mcp-remote, screenshot) with a task "
        "prompt and return a trimmed summary of its output."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "harness": {
                "type": "string",
                "description": (
                    "Name of a registered harness "
                    "('open-interpreter', 'python-script', 'mcp-remote', 'screenshot')."
                ),
            },
            "prompt": {
                "type": "string",
                "description": "Die Aufgabe / der Prompt für den Sub-Agenten.",
            },
            "cwd": {
                "type": "string",
                "description": "Working-Directory (optional).",
                "default": "",
            },
            "timeout_s": {
                "type": "number",
                "description": "Maximale Laufzeit in Sekunden (Default 600).",
                "default": 600,
            },
            "parallel_harnesses": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Wenn gesetzt, werden alle genannten Harnesses parallel ausgeführt "
                    "und ihre Outputs aggregiert. 'harness' wird ignoriert."
                ),
                "default": [],
            },
            "aggregation": {
                "type": "string",
                "enum": ["merge", "first_success"],
                "default": "merge",
            },
        },
        "required": ["harness", "prompt"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        manager: HarnessManager | None = None,
        max_output_chars: int = 4000,
    ) -> None:
        self._bus = bus
        self._manager = manager or HarnessManager(bus=bus)
        self._max_output_chars = max_output_chars

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trim(self, text: str) -> str:
        if len(text) <= self._max_output_chars:
            return text
        keep = self._max_output_chars // 2
        return text[:keep] + f"\n\n[… {len(text) - 2 * keep} chars gekürzt …]\n\n" + text[-keep:]

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        harness_name = (args.get("harness") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        parallel = args.get("parallel_harnesses") or []
        aggregation = args.get("aggregation") or "merge"
        cwd = args.get("cwd") or "."
        timeout_s = float(args.get("timeout_s") or 600)
        # Optional env passthrough (not in the LLM-facing schema — set
        # programmatically by internal callers). The computer_use tool /
        # local-action gate use it to thread the turn's resolved output language
        # to the in-harness verifier (JARVIS_OUTPUT_LANGUAGE).
        raw_env = args.get("env")
        env = dict(raw_env) if isinstance(raw_env, dict) else {}

        if not prompt:
            return ToolResult(success=False, output=None, error="prompt fehlt")

        task = HarnessTask(
            prompt=prompt,
            cwd=cwd,
            timeout_s=timeout_s,
            env=env,
        )

        try:
            if parallel:
                return await self._execute_parallel(list(parallel), task, aggregation)
            if not harness_name:
                return ToolResult(success=False, output=None, error="harness fehlt")
            return await self._execute_single(harness_name, task)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=f"{type(exc).__name__}: {exc}")

    async def _execute_single(self, name: str, task: HarnessTask) -> ToolResult:
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        exit_code = -1
        cost_usd = 0.0
        duration_ms = 0
        timeout_s = max(0.001, float(task.timeout_s))
        deadline = time.monotonic() + timeout_s
        started_ns = time.time_ns()

        try:
            stream = self._manager.dispatch(name, task)
            while True:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise asyncio.TimeoutError
                try:
                    result = await asyncio.wait_for(anext(stream), timeout=remaining_s)
                except StopAsyncIteration:
                    break
                if result.stdout:
                    stdout_buf.append(result.stdout)
                if result.stderr:
                    stderr_buf.append(result.stderr)
                if result.is_final:
                    exit_code = result.exit_code
                    duration_ms = result.duration_ms
                if result.cost_usd:
                    cost_usd += result.cost_usd
        except KeyError as exc:
            # A missing harness must NOT leak the raw HarnessManager.get()
            # message — it embeds the internal active/failed harness lists, and
            # this error can reach the voice path. Log the detail; return a
            # neutral, scrub-safe error (no internal harness inventory).
            log.warning("dispatch_to_harness: unknown harness %r — %s", name, exc)
            return ToolResult(
                success=False,
                output=None,
                error=f"harness {name!r} is not set up here",
            )
        except asyncio.TimeoutError:
            duration_ms = (time.time_ns() - started_ns) // 1_000_000
            combined_stdout = self._trim("".join(stdout_buf).strip())
            timeout_msg = f"timeout after {timeout_s:.3g}s"
            combined_stderr = self._trim(
                "\n".join(s for s in ("".join(stderr_buf).strip(), timeout_msg) if s)
            )
            return ToolResult(
                success=False,
                output={
                    "harness": name,
                    "exit_code": _TIMEOUT_EXIT_CODE,
                    "stdout": combined_stdout,
                    "stderr": combined_stderr[:1000],
                    "cost_usd": round(cost_usd, 4),
                    "duration_ms": duration_ms,
                },
                error=timeout_msg,
            )
        finally:
            if "stream" in locals():
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        combined_stdout = self._trim("".join(stdout_buf).strip())
        combined_stderr = self._trim("".join(stderr_buf).strip())
        return ToolResult(
            success=exit_code == 0,
            output={
                "harness": name,
                "exit_code": exit_code,
                "stdout": combined_stdout,
                "stderr": combined_stderr[:1000],
                "cost_usd": round(cost_usd, 4),
                "duration_ms": duration_ms,
            },
            error=None if exit_code == 0 else f"exit {exit_code}",
        )

    async def _execute_parallel(
        self, names: list[str], task: HarnessTask, aggregation: str
    ) -> ToolResult:
        buffers: dict[str, dict[str, Any]] = {n: {"stdout": [], "stderr": [], "exit": -1}
                                              for n in names}
        timeout_s = max(0.001, float(task.timeout_s))
        deadline = time.monotonic() + timeout_s

        try:
            stream = self._manager.dispatch_parallel(names, task, aggregation=aggregation)
            while True:
                remaining_s = deadline - time.monotonic()
                if remaining_s <= 0:
                    raise asyncio.TimeoutError
                try:
                    name, result = await asyncio.wait_for(
                        anext(stream),
                        timeout=remaining_s,
                    )
                except StopAsyncIteration:
                    break
                buf = buffers.setdefault(name, {"stdout": [], "stderr": [], "exit": -1})
                if result.stdout:
                    buf["stdout"].append(result.stdout)
                if result.stderr:
                    buf["stderr"].append(result.stderr)
                if result.is_final:
                    buf["exit"] = result.exit_code
        except asyncio.TimeoutError:
            timeout_msg = f"timeout after {timeout_s:.3g}s"
            for buf in buffers.values():
                if buf["exit"] == -1:
                    buf["exit"] = _TIMEOUT_EXIT_CODE
                    buf["stderr"].append(timeout_msg)
            return self._parallel_result(
                names,
                aggregation,
                buffers,
                success=False,
                error=timeout_msg,
            )
        finally:
            if "stream" in locals():
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        all_ok = all(b["exit"] == 0 for b in buffers.values())
        return self._parallel_result(
            names,
            aggregation,
            buffers,
            success=all_ok,
            error=None if all_ok else "ein oder mehr Harnesses mit non-zero exit",
        )

    def _parallel_result(
        self,
        names: list[str],
        aggregation: str,
        buffers: dict[str, dict[str, Any]],
        *,
        success: bool,
        error: str | None,
    ) -> ToolResult:
        out_lines: list[str] = []
        for n, b in buffers.items():
            body = self._trim("".join(b["stdout"] + b["stderr"]).strip())
            out_lines.append(f"## {n} (exit={b['exit']})\n{body}")

        return ToolResult(
            success=success,
            output={
                "harnesses": names,
                "aggregation": aggregation,
                "combined": "\n\n".join(out_lines),
                "per_harness": {
                    n: {"exit": b["exit"],
                        "stdout_len": sum(len(s) for s in b["stdout"]),
                        "stderr_len": sum(len(s) for s in b["stderr"])}
                    for n, b in buffers.items()
                },
            },
            error=error,
        )
