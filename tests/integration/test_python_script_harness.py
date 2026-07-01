"""Live integration test for the python-script harness (a real Python subprocess)."""
from __future__ import annotations

import pytest

from jarvis.core.protocols import HarnessTask
from jarvis.plugins.harness.python_script import PythonScriptHarness


@pytest.mark.asyncio
async def test_python_script_executes_inline():
    h = PythonScriptHarness()
    task = HarnessTask(
        prompt="print('hello from subprocess'); x=2+2; print(f'result={x}')",
        timeout_s=30,
    )
    out = []
    err = []
    final_exit = None
    async for r in h.invoke(task):
        if r.stdout:
            out.append(r.stdout)
        if r.stderr:
            err.append(r.stderr)
        if r.is_final:
            final_exit = r.exit_code

    stdout = "".join(out)
    assert "hello from subprocess" in stdout
    assert "result=4" in stdout
    assert final_exit == 0


@pytest.mark.asyncio
async def test_python_script_captures_nonzero_exit():
    h = PythonScriptHarness()
    task = HarnessTask(
        prompt="import sys; sys.exit(2)",
        timeout_s=15,
    )
    final_exit = None
    async for r in h.invoke(task):
        if r.is_final:
            final_exit = r.exit_code
    assert final_exit == 2


@pytest.mark.asyncio
async def test_python_script_captures_stderr():
    h = PythonScriptHarness()
    task = HarnessTask(
        prompt="import sys; print('STDERR!', file=sys.stderr)",
        timeout_s=15,
    )
    err = []
    async for r in h.invoke(task):
        if r.stderr:
            err.append(r.stderr)
    joined = "".join(err)
    assert "STDERR" in joined
