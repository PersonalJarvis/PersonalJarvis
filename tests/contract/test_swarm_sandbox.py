"""The same real Wasm boundary must enforce isolation on all supported OSes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from jarvis.swarm.sandbox import QUICKJS_PATH, QUICKJS_SHA256, WasmSandbox


def test_bundled_interpreter_is_pinned_and_offline() -> None:
    assert hashlib.sha256(QUICKJS_PATH.read_bytes()).hexdigest() == QUICKJS_SHA256
    assert QUICKJS_PATH.with_name("QuickJS-LICENSE.txt").is_file()
    assert WasmSandbox().capability()["available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "Infinity", "{nested:NaN}"])
async def test_non_finite_outputs_cannot_silently_become_null(value):
    result = await WasmSandbox().run("function main(){return " + value + ";}", {})
    assert result.exit_code != 0
    assert "Non-finite" in result.stderr


@pytest.mark.asyncio
async def test_explicit_null_remains_a_valid_json_result():
    result = await WasmSandbox().run("function main(){return null;}", {})
    assert result.exit_code == 0
    assert result.output is None


@pytest.mark.asyncio
async def test_real_javascript_result_and_capture() -> None:
    result = await WasmSandbox().run(
        'function main(input) { print("verified computation"); '
        "return {sum: input.values.reduce((a,b)=>a+b,0)}; }",
        {"values": [1, 3, 7]},
    )
    assert result.exit_code == 0, result.stderr
    assert result.output == {"sum": 11}
    assert result.stdout == "verified computation"
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_no_ambient_host_apis_or_cross_invocation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWARM_SECRET_CANARY", "private-host-value")
    sandbox = WasmSandbox()
    first = await sandbox.run(
        "function main(){globalThis.privateState='first-team';return true}", {}
    )
    assert first.exit_code == 0
    second = await sandbox.run(
        """function main(){return {
        secret:typeof process, fs:typeof require, sockets:typeof fetch,
        host:typeof Deno, std:typeof std, os:typeof os,
        previous:typeof globalThis.privateState};}""",
        {},
    )
    assert second.exit_code == 0, second.stderr
    assert set(second.output.values()) == {"undefined"}
    assert "private-host-value" not in json.dumps(second.output)


@pytest.mark.asyncio
async def test_no_host_file_reads_or_writes(tmp_path) -> None:
    secret = tmp_path / "private.txt"
    secret.write_text("private host content", encoding="utf-8")
    result = await WasmSandbox().run(
        "function main(x){return require('fs').readFileSync(x.path,'utf8')}",
        {"path": str(secret)},
    )
    assert result.exit_code != 0
    assert result.output is None
    assert "private host content" not in result.stdout + result.stderr
    assert secret.read_text(encoding="utf-8") == "private host content"


@pytest.mark.asyncio
async def test_fuel_and_memory_are_hard_limits() -> None:
    exhausted = await WasmSandbox(fuel=200_000).run("function main(){while(true){}}", {})
    assert exhausted.exit_code != 0
    assert exhausted.output is None
    memory = await WasmSandbox(memory_bytes=32 * 1024 * 1024).run(
        "function main(){return new Uint8Array(1024*1024*1024).length}",
        {},
    )
    assert memory.exit_code != 0
    assert memory.output is None


@pytest.mark.asyncio
async def test_output_is_bounded_and_failed_output_not_accepted() -> None:
    result = await WasmSandbox(max_output_bytes=4096).run(
        'function main(){while(true){print("x".repeat(10000))}}',
        {},
    )
    assert result.exit_code != 0
    assert result.output is None
    assert len(result.stdout.encode()) <= 4096


@pytest.mark.asyncio
async def test_timeout_and_cancel_drain_execution() -> None:
    sandbox = WasmSandbox(fuel=10**12)
    timeout = await sandbox.run("function main(){while(true){}}", {}, timeout_s=0.05)
    assert timeout.exit_code == 124
    start = time.monotonic()
    canceled = await sandbox.run(
        "function main(){while(true){}}", {}, cancel=lambda: time.monotonic() - start > 0.05
    )
    assert canceled.exit_code == 130
    healthy = await sandbox.run("function main(){return 42}", {})
    assert healthy.exit_code == 0
    assert healthy.output == 42


@pytest.mark.asyncio
async def test_asyncio_cancellation_does_not_leave_guest_running() -> None:
    execution = asyncio.create_task(
        WasmSandbox(fuel=10**12).run("function main(){while(true){}}", {})
    )
    await asyncio.sleep(0.05)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, 3)


@pytest.mark.asyncio
async def test_clocks_randomness_and_inputs_are_reproducible() -> None:
    sandbox = WasmSandbox()
    source = "function main(x){return [Date.now(),Math.random(),x.value]}"
    a, b = await asyncio.gather(
        sandbox.run(source, {"value": "team-a"}), sandbox.run(source, {"value": "team-a"})
    )
    assert a.exit_code == b.exit_code == 0
    assert a.output == b.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "function other(){return true}",
        "async function main(){return true}",
        "function main(){return undefined}",
        "function main(){throw Error('failed verification')}",
    ],
)
async def test_no_success_without_unique_json_result(source: str) -> None:
    result = await WasmSandbox().run(source, {})
    assert result.exit_code != 0
    assert result.output is None


@pytest.mark.asyncio
async def test_inputs_and_source_are_bounded() -> None:
    sandbox = WasmSandbox(max_input_bytes=50)
    with pytest.raises(ValueError, match="input limit"):
        await sandbox.run("function main(x){return x}", {"value": "x" * 100})
    with pytest.raises(ValueError, match="source"):
        await sandbox.run("x" * 100001, {})
