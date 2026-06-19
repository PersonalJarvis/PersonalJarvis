"""Recursive Reflector: LLM prompt -> Python code -> subprocess REPL -> Verdict."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from tests.fakes.llm import FakeLLM
from skillbook.ace_core.models import Verdict
from skillbook.ace_core.reflector import RecursiveReflector
from skillbook.memory_layer.models import TraceStep
from skillbook.memory_layer.store import SQLiteMemoryStore


@pytest.fixture
async def memory(tmp_path: Path):
    s = SQLiteMemoryStore(db_path=tmp_path / "r.db")
    await s.open()
    yield s
    await s.close()


async def test_fake_llm_emits_python_code_for_known_prompt() -> None:
    llm = FakeLLM()
    code = await llm.complete("analyze trace at SKB_TRACE_PATH and emit a Verdict JSON")
    assert "SKB_TRACE_PATH" in code
    assert "print(" in code
    assert "json.dumps" in code or "json.dump" in code


async def test_reflector_with_timeout_trace_produces_retry_rule_verdict(
    memory: SQLiteMemoryStore,
) -> None:
    for idx, status in enumerate(["BLOCKED_BY_GUARDRAIL"]):
        await memory.put_trace_step(
            TraceStep(
                task_id="t_reflect",
                step_idx=idx,
                actor="magic_home_controller",
                params={},
                result={"error": "timeout"},
                status=status,
                ts_ns=time.time_ns(),
            )
        )
    reflector = RecursiveReflector(memory=memory, llm=FakeLLM(), timeout_s=10.0)

    verdict = await reflector.reflect(task_id="t_reflect")

    assert isinstance(verdict, Verdict)
    assert verdict.outcome == "failure"
    assert verdict.rule is not None
    assert verdict.rule["trigger"]["actor"] == "magic_home_controller"
    assert verdict.rule["strategy"]["kind"] == "retry_with_delay"


async def test_reflector_with_all_ok_trace_produces_no_action(
    memory: SQLiteMemoryStore,
) -> None:
    await memory.put_trace_step(
        TraceStep(
            task_id="t_ok",
            step_idx=0,
            actor="ok_actor",
            params={},
            result={"value": 1},
            status="OK",
            ts_ns=time.time_ns(),
        )
    )
    reflector = RecursiveReflector(memory=memory, llm=FakeLLM(), timeout_s=10.0)

    verdict = await reflector.reflect(task_id="t_ok")

    assert verdict.outcome == "no_action"
    assert verdict.rule is None


async def test_reflector_sandbox_runs_analysis_in_subprocess(
    memory: SQLiteMemoryStore, tmp_path: Path
) -> None:
    """Verify that the sandbox is actually a subprocess by smuggling a marker."""
    await memory.put_trace_step(
        TraceStep(
            task_id="t_pid",
            step_idx=0,
            actor="some_actor",
            params={},
            result={"error": "x"},
            status="BLOCKED_BY_GUARDRAIL",
            ts_ns=time.time_ns(),
        )
    )

    class _PidLeakLLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            return (
                "import json, os, sys\n"
                "verdict = {\n"
                "    'outcome': 'failure',\n"
                "    'evidence': 'pid=' + str(os.getpid()),\n"
                "    'rule': {'trigger': {'actor': 'x'}, 'strategy': {'kind': 'retry_with_delay'}}\n"
                "}\n"
                "print(json.dumps(verdict))\n"
            )

    reflector = RecursiveReflector(memory=memory, llm=_PidLeakLLM(), timeout_s=10.0)
    verdict = await reflector.reflect(task_id="t_pid")
    leaked_pid = int(verdict.evidence.split("=")[-1])
    import os as _os
    assert leaked_pid != _os.getpid(), "sandbox must run in a separate process"


async def test_reflector_sandbox_enforces_timeout(memory: SQLiteMemoryStore) -> None:
    await memory.put_trace_step(
        TraceStep(
            task_id="t_hang", step_idx=0, actor="x", params={}, result={},
            status="BLOCKED_BY_GUARDRAIL", ts_ns=time.time_ns(),
        )
    )

    class _HangLLM:
        async def complete(self, prompt: str, *, max_tokens: int = 800) -> str:
            return "import time\nwhile True:\n    time.sleep(1)\n"

    reflector = RecursiveReflector(memory=memory, llm=_HangLLM(), timeout_s=0.5)
    with pytest.raises(asyncio.TimeoutError):
        await reflector.reflect(task_id="t_hang")
