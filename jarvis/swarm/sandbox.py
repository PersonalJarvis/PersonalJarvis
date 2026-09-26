"""Ephemeral JavaScript execution inside a capability-empty Wasm instance.

The bundled QuickJS interpreter executes *inside* Wasmtime. Generated source
never runs in Python, Node or a host shell. No WASI environment, filesystem,
stdin, sockets or process capabilities are inherited. Data leaves only through
bounded output, whose acceptance is the independent verifier's responsibility.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.core.swarm_types import SandboxResult

logger = logging.getLogger(__name__)
QUICKJS_VERSION = "0.16.2"
QUICKJS_SHA256 = "d2939e98c808e8b9f4164cd0d7b0398cbc0121ddf52862bcd92157d923e461cc"
QUICKJS_PATH = Path(__file__).with_name("vendor") / "qjs-wasi.wasm"


class _ExecutionControl:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.engine: Any = None
        self.stopped = False

    def stop(self) -> None:
        with self.lock:
            self.stopped = True
            if self.engine is not None:
                self.engine.increment_epoch()


class WasmSandbox:
    """Fresh memory and deterministic WASI imports for each invocation."""

    def __init__(
        self,
        *,
        memory_bytes: int = 64 * 1024 * 1024,
        fuel: int = 100_000_000,
        max_output_bytes: int = 262_144,
        max_input_bytes: int = 1_048_576,
        offload: Callable[..., Any] = asyncio.to_thread,
    ) -> None:
        if min(memory_bytes, fuel, max_output_bytes, max_input_bytes) <= 0:
            raise ValueError("Sandbox limits must be positive")
        self.memory_bytes = memory_bytes
        self.offload = offload
        self.fuel = fuel
        self.max_output_bytes = max_output_bytes
        self.max_input_bytes = max_input_bytes

    def capability(self) -> dict[str, Any]:
        """Probe the installed offline runtime without executing generated code."""
        try:
            import wasmtime

            if hashlib.sha256(QUICKJS_PATH.read_bytes()).hexdigest() != QUICKJS_SHA256:
                raise ValueError("Bundled JavaScript runtime integrity check failed")
            # Construction catches unsupported JIT/platform configurations.
            with wasmtime.Engine():
                pass
            return {"available": True, "runtime": "wasmtime-quickjs", "version": QUICKJS_VERSION}
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("Swarm sandbox capability unavailable: %s", exc)
            return {"available": False, "reason": str(exc), "runtime": "wasmtime-quickjs"}

    async def run(
        self,
        script: str,
        inputs: Any,
        *,
        timeout_s: float = 10,
        cancel: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        started = time.monotonic()
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
            raise ValueError("Sandbox timeout must be between 0 and 120 seconds")
        if not isinstance(script, str) or len(script.encode("utf-8")) > 100_000:
            raise ValueError("JavaScript source exceeds 100000 bytes")
        input_json = json.dumps(inputs, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(input_json.encode("utf-8")) > self.max_input_bytes:
            raise ValueError("Sandbox input limit exceeded")
        if cancel is not None and cancel():
            return SandboxResult(None, "", "Execution canceled", 130, 0)
        control = _ExecutionControl()
        execution = asyncio.create_task(self.offload(self._execute, script, input_json, control))
        reason = ""
        try:
            while not execution.done():
                if cancel is not None and cancel():
                    reason = "Execution canceled"
                    control.stop()
                    break
                if time.monotonic() - started >= timeout_s:
                    reason = "Execution timed out"
                    control.stop()
                    break
                await asyncio.wait({execution}, timeout=0.02)
            # Never return while generated code is still executing in a thread.
            result = await asyncio.shield(execution)
        except asyncio.CancelledError:
            control.stop()
            await asyncio.shield(execution)
            raise
        duration = (time.monotonic() - started) * 1000
        if reason:
            return SandboxResult(
                None, result.stdout, reason, 130 if "canceled" in reason else 124, duration
            )
        return SandboxResult(
            result.output, result.stdout, result.stderr, result.exit_code, duration
        )

    def _execute(self, script: str, input_json: str, control: _ExecutionControl) -> SandboxResult:
        import wasmtime

        raw = QUICKJS_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != QUICKJS_SHA256:
            raise RuntimeError("Bundled JavaScript runtime integrity check failed")
        config = wasmtime.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        config.wasm_threads = False
        config.wasm_relaxed_simd = False
        config.cranelift_nan_canonicalization = True
        output = bytearray()
        errors = bytearray()
        overflow = False

        def capture(target: bytearray, data: bytes) -> int:
            nonlocal overflow
            remaining = self.max_output_bytes - len(output) - len(errors)
            target.extend(data[: max(0, remaining)])
            if len(data) > remaining:
                overflow = True
                control.stop()
                return -1
            return len(data)

        marker = "__swarm_result_" + uuid.uuid4().hex + ":"
        # Capture pristine builtins outside the source's strict lexical scope.
        # An absent result, rejected Promise, or non-JSON result fails closed.
        wrapper = (
            "(function(){'use strict';const emit=globalThis.print;"
            "const encode=JSON.stringify;const parse=JSON.parse;const finite=Number.isFinite;"
            f"const data=parse({json.dumps(input_json)});"
            "const answer=(function(input){'use strict';\n" + script + "\n"
            "if(typeof main!=='function')throw Error('Define main(input)');"
            "return main(input);})(data);"
            "if(answer&&typeof answer.then==='function')throw Error('Return synchronous JSON');"
            "const encoded=encode(answer,(_key,value)=>{"
            "if(typeof value==='number'&&!finite(value))throw Error("
            "'Non-finite result: check input shape and arithmetic; return finite JSON');"
            "return value;});"
            "if(encoded===undefined)throw Error('Return a JSON value');"
            f"emit({json.dumps(marker)}+encoded);"
            "})();"
        )
        code = 0
        with wasmtime.Engine(config) as engine:
            with control.lock:
                control.engine = engine
            try:
                module = wasmtime.Module(engine, raw)
                with wasmtime.Store(engine) as store, wasmtime.Linker(engine) as linker:
                    store.set_limits(
                        memory_size=self.memory_bytes,
                        instances=1,
                        memories=1,
                        tables=1,
                        table_elements=10000,
                    )
                    store.set_fuel(self.fuel)
                    store.set_epoch_deadline(0 if control.stopped else 1)
                    wasi = wasmtime.WasiConfig()
                    wasi.argv = ["qjs", "--script", "-e", wrapper]
                    wasi.env = []
                    wasi.stdout_custom = lambda data: capture(output, data)
                    wasi.stderr_custom = lambda data: capture(errors, data)
                    store.set_wasi(wasi)
                    linker.define_wasi()
                    self._deterministic_imports(linker, wasmtime)
                    instance = linker.instantiate(store, module)
                    instance.exports(store)["_start"](store)
            except wasmtime.ExitTrap as exc:
                # WASI exit, including exit(0), is a captured process result.
                code = exc.code
            except wasmtime.Trap as exc:
                code = 137
                capture(errors, ("Sandbox resource limit or trap: " + str(exc)).encode("utf-8"))
            except wasmtime.WasmtimeError as exc:
                code = 1
                capture(errors, ("Sandbox execution failed: " + str(exc)).encode("utf-8"))
            finally:
                with control.lock:
                    control.engine = None
        stdout = output.decode("utf-8", errors="replace")
        stderr = errors.decode("utf-8", errors="replace")
        lines = stdout.splitlines()
        records = [line[len(marker) :] for line in lines if line.startswith(marker)]
        stdout = "\n".join(line for line in lines if not line.startswith(marker))
        if overflow:
            return SandboxResult(None, stdout, "Sandbox output limit exceeded", 137, 0)
        if code:
            return SandboxResult(None, stdout, stderr, code, 0)
        if len(records) != 1:
            return SandboxResult(None, stdout, "Sandbox produced no unique JSON result", 1, 0)
        try:
            value = json.loads(records[0])
        except (ValueError, RecursionError):
            # Return the parse failure through the bounded execution result.
            return SandboxResult(None, stdout, "Sandbox result is invalid JSON", 1, 0)
        return SandboxResult(value, stdout, stderr, 0, 0)

    @staticmethod
    def _deterministic_imports(linker: Any, wasm: Any) -> None:
        """Remove ambient time/random data at the actual WASI import boundary."""
        linker.allow_shadowing = True
        counter = 0

        def write(caller: Any, pointer: int, data: bytes) -> int:
            memory = caller.get("memory")
            if memory is None or pointer < 0 or pointer + len(data) > memory.data_len(caller):
                return 21  # WASI EFAULT
            memory.write(caller, data, pointer)
            return 0

        def random_get(caller: Any, pointer: int, size: int) -> int:
            nonlocal counter
            if size < 0 or size > 65536:
                return 28  # WASI EINVAL; no unbounded host allocation
            data = bytearray()
            while len(data) < size:
                data.extend(hashlib.sha256(str(counter).encode("ascii")).digest())
                counter += 1
            return write(caller, pointer, bytes(data[:size]))

        linker.define_func(
            "wasi_snapshot_preview1",
            "random_get",
            wasm.FuncType([wasm.ValType.i32(), wasm.ValType.i32()], [wasm.ValType.i32()]),
            random_get,
            access_caller=True,
        )
        linker.define_func(
            "wasi_snapshot_preview1",
            "clock_time_get",
            wasm.FuncType(
                [wasm.ValType.i32(), wasm.ValType.i64(), wasm.ValType.i32()], [wasm.ValType.i32()]
            ),
            lambda caller, clock, precision, pointer: write(caller, pointer, bytes(8)),
            access_caller=True,
        )


# Public concise alias used by the runtime factory.
Sandbox = WasmSandbox
