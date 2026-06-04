"""MultiSpawnTool: N parallele Harness-Calls mit unterschiedlichen Prompts.

Unterschied zu `dispatch_to_harness`:
    - `dispatch_to_harness` = 1 Harness, 1 Prompt (oder N Harnesses × 1 Prompt).
    - `multi_spawn`         = 1 Harness, N unterschiedliche Prompts parallel.

Anwendungsfall: OpenClaw-Worker spawnt 3 parallele `openclaw`-Agents mit
verschiedenen Teil-Aufgaben ("schreib die Tests", "schreib die Impl",
"schreib die Docs") und aggregiert die Outputs.

Aggregation-Modes:
    - "merge"         → alle Sections mit "---"-Separator konkatenieren.
    - "first_success" → ersten Section mit exit=0 zurückgeben, Rest cancellen.

Output-Cap: `max_output_chars` (Default 8000). Wenn überschritten, werden
spätere Sections mit "(X sections truncated)"-Marker ersetzt.
"""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, HarnessTask, ToolResult
from jarvis.harness.manager import HarnessManager


class MultiSpawnTool:
    name: str = "multi_spawn"
    risk_tier: str = "monitor"
    description: str = (
        "Führt N parallele Harness-Calls aus (z.B. 3x openclaw mit "
        "unterschiedlichen Prompts). Für Fan-Out von Sub-Tasks."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "harness": {
                "type": "string",
                "enum": ["openclaw", "codex", "python-script"],
                "description": "Welches Harness N-fach spawnen.",
            },
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 5,
                "description": (
                    "Liste von Prompts; pro Prompt wird ein Harness-Call "
                    "parallel gestartet."
                ),
            },
            "aggregation": {
                "type": "string",
                "enum": ["merge", "first_success"],
                "default": "merge",
            },
            "timeout_s": {"type": "integer", "default": 600},
        },
        "required": ["harness", "prompts"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        manager: HarnessManager | None = None,
        max_output_chars: int = 8000,
    ) -> None:
        self._bus = bus
        self._manager = manager or HarnessManager(bus=bus)
        self._max_output_chars = max_output_chars

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        harness_name = (args.get("harness") or "").strip()
        prompts_raw = args.get("prompts") or []
        aggregation = args.get("aggregation") or "merge"
        timeout_s = int(args.get("timeout_s") or 600)

        if not harness_name:
            return ToolResult(success=False, output=None, error="harness fehlt")

        prompts = [p for p in prompts_raw if isinstance(p, str) and p.strip()]
        if len(prompts) < 2:
            return ToolResult(
                success=False,
                output=None,
                error="mindestens 2 Prompts erforderlich",
            )

        tasks = [HarnessTask(prompt=p, timeout_s=timeout_s) for p in prompts]

        try:
            if aggregation == "first_success":
                return await self._execute_first_success(harness_name, tasks)
            return await self._execute_merge(harness_name, tasks)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _collect(self, harness_name: str, task: HarnessTask) -> dict[str, Any]:
        """Drains den Dispatch-Stream zu einem akkumulierten Result-Dict."""
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        exit_code = -1
        duration_ms = 0
        cost_usd = 0.0
        try:
            async for r in self._manager.dispatch(harness_name, task):
                if r.stdout:
                    stdout_buf.append(r.stdout)
                if r.stderr:
                    stderr_buf.append(r.stderr)
                if r.is_final:
                    exit_code = r.exit_code
                    duration_ms = r.duration_ms
                if r.cost_usd:
                    cost_usd += r.cost_usd
        except Exception as exc:  # noqa: BLE001
            stderr_buf.append(f"Dispatch-Crash: {exc}\n")
            exit_code = 1
        return {
            "stdout": "".join(stdout_buf),
            "stderr": "".join(stderr_buf),
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "cost_usd": cost_usd,
        }

    async def _execute_merge(
        self, harness_name: str, tasks: list[HarnessTask]
    ) -> ToolResult:
        total = len(tasks)
        collected = await asyncio.gather(
            *[self._collect(harness_name, t) for t in tasks],
            return_exceptions=False,
        )

        aggregated, truncated_count = self._aggregate_with_cap(collected, total)
        all_ok = all(c["exit_code"] == 0 for c in collected)

        artifacts = tuple(
            f"section-{i + 1}: exit={c['exit_code']} "
            f"duration_ms={c['duration_ms']} "
            f"cost_usd={round(c['cost_usd'], 4)}"
            for i, c in enumerate(collected)
        )

        return ToolResult(
            success=all_ok,
            output={
                "harness": harness_name,
                "aggregation": "merge",
                "sections_total": total,
                "sections_truncated": truncated_count,
                "combined": aggregated,
                "per_section": [
                    {
                        "index": i + 1,
                        "exit_code": c["exit_code"],
                        "stdout_len": len(c["stdout"]),
                        "stderr_len": len(c["stderr"]),
                        "duration_ms": c["duration_ms"],
                    }
                    for i, c in enumerate(collected)
                ],
            },
            error=None if all_ok else "ein oder mehr Sections mit non-zero exit",
            artifacts=artifacts,
        )

    async def _execute_first_success(
        self, harness_name: str, tasks: list[HarnessTask]
    ) -> ToolResult:
        total = len(tasks)
        pending = [
            asyncio.create_task(
                self._collect(harness_name, t), name=f"multi-spawn-{i}"
            )
            for i, t in enumerate(tasks)
        ]

        winning: dict[str, Any] | None = None
        winning_index = -1
        completed: list[tuple[int, dict[str, Any]]] = []

        try:
            while pending:
                done, pending_set = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                pending = list(pending_set)
                for d in done:
                    idx = next(
                        (i for i, t in enumerate(tasks) if d.get_name() == f"multi-spawn-{i}"),
                        -1,
                    )
                    result_dict = await d
                    completed.append((idx, result_dict))
                    if (
                        winning is None
                        and result_dict["exit_code"] == 0
                    ):
                        winning = result_dict
                        winning_index = idx
                        break
                if winning is not None:
                    break
        finally:
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if winning is None:
            last = completed[-1][1] if completed else {
                "stdout": "",
                "stderr": "alle Sections fehlgeschlagen",
                "exit_code": 1,
            }
            combined = self._trim_section(
                self._format_section(1, 1, last["stdout"] or last["stderr"])
            )
            return ToolResult(
                success=False,
                output={
                    "harness": harness_name,
                    "aggregation": "first_success",
                    "sections_total": total,
                    "sections_truncated": total - len(completed),
                    "combined": combined,
                    "winning_index": None,
                },
                error="kein Section war erfolgreich",
            )

        combined = self._trim_section(
            self._format_section(winning_index + 1, total, winning["stdout"])
        )
        return ToolResult(
            success=True,
            output={
                "harness": harness_name,
                "aggregation": "first_success",
                "sections_total": total,
                "sections_truncated": total - 1,
                "combined": combined,
                "winning_index": winning_index + 1,
            },
            error=None,
            artifacts=(
                f"winning-section: index={winning_index + 1} "
                f"exit={winning['exit_code']} "
                f"duration_ms={winning['duration_ms']}",
            ),
        )

    def _format_section(self, index: int, total: int, body: str) -> str:
        return f"---\nSection {index}/{total}:\n{body.strip()}"

    def _trim_section(self, section: str) -> str:
        if len(section) <= self._max_output_chars:
            return section
        marker = "\n\n[… truncated …]"
        keep = self._max_output_chars - len(marker)
        return section[:keep] + marker

    def _aggregate_with_cap(
        self, collected: list[dict[str, Any]], total: int
    ) -> tuple[str, int]:
        """Formatiert Sections und cap'd aggregate auf max_output_chars.

        Returns: (aggregated_text, truncated_section_count)
        """
        parts: list[str] = []
        current_len = 0
        truncated_count = 0

        for i, c in enumerate(collected):
            body = c["stdout"].strip() or c["stderr"].strip() or "(no output)"
            section = self._format_section(i + 1, total, body)
            projected = current_len + len(section) + (2 if parts else 0)
            if projected > self._max_output_chars:
                truncated_count = total - i
                break
            parts.append(section)
            current_len = projected

        aggregated = "\n\n".join(parts) if parts else ""

        if truncated_count > 0:
            marker = f"\n\n[{truncated_count} sections truncated]"
            if len(aggregated) + len(marker) > self._max_output_chars:
                overflow = (len(aggregated) + len(marker)) - self._max_output_chars
                if aggregated:
                    aggregated = aggregated[: -overflow] if overflow < len(aggregated) else ""
            aggregated += marker

        return aggregated, truncated_count
