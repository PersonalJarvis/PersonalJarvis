"""RecursiveReflector: trace analysis in a subprocess Python REPL sandbox (ADR-0007).

The Reflector's job is to look at a Generator trace, decide whether the task
succeeded or failed (and if so, why), and propose a corrective Rule. The
distinguishing innovation per the architecture survey is that the analysis is
*Python code executed over the trace*, not a free-form LLM paraphrase — so
this module spawns a subprocess and reads a structured Verdict from stdout.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skillbook.memory_layer.store import MemoryStore

from .llm import LLM
from .models import Verdict

_PROMPT_TEMPLATE = """\
You are the Recursive Reflector for an autonomous agent. Analyze the JSON trace
at the environment variable SKB_TRACE_PATH. Each element is one execution step
with fields task_id, step_idx, actor, params, result, status, ts_ns.

Identify steps with status in {{"TIMEOUT", "BLOCKED_BY_GUARDRAIL"}} and emit a
single-line JSON Verdict on stdout with these fields:
  - outcome: "failure" if any failure was observed, "no_action" otherwise.
  - evidence: human-readable English summary.
  - rule: null, or an object {{trigger: ..., strategy: ...}} proposing a fix.

Write Python source code only. Do not include explanations.

Trace summary ({n_steps} steps):
{trace_summary}
"""


@dataclass(slots=True)
class RecursiveReflector:
    memory: MemoryStore
    llm: LLM
    timeout_s: float = 8.0

    async def reflect(self, *, task_id: str) -> Verdict:
        steps = await self.memory.query_trace_steps(task_id=task_id)
        trace_payload = [s.model_dump(mode="json") for s in steps]

        prompt = _PROMPT_TEMPLATE.format(
            n_steps=len(trace_payload),
            trace_summary=json.dumps(trace_payload, indent=2),
        )
        code = await self.llm.complete(prompt)

        return await self._run_in_sandbox(code, trace_payload)

    async def _run_in_sandbox(
        self,
        code: str,
        trace_payload: list[dict],
    ) -> Verdict:
        with tempfile.TemporaryDirectory(prefix="skb_reflector_") as td:
            tmpdir = Path(td)
            trace_path = tmpdir / "trace.json"
            trace_path.write_text(json.dumps(trace_payload), encoding="utf-8")

            env = {
                "PATH": os.environ.get("PATH", ""),
                "SKB_TRACE_PATH": str(trace_path),
                "PYTHONHASHSEED": "0",
                "PYTHONIOENCODING": "utf-8",
            }
            # Windows requires SYSTEMROOT for some stdlib internals (e.g., random).
            if "SYSTEMROOT" in os.environ:
                env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",  # isolated mode: ignore PYTHON* env, ignore user site
                "-c",
                code,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmpdir),
                env=env,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                raise

            if proc.returncode != 0:
                err = stderr_b.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Reflector sandbox exited with code {proc.returncode}: {err}"
                )

            text = stdout_b.decode("utf-8", errors="replace").strip()
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if not lines:
                raise RuntimeError("Reflector sandbox produced no output")
            return Verdict.model_validate_json(lines[-1])
