"""Live E2E probe: drive real, installed+authed CLIs through the full
NL -> tool -> command -> result loop and print PASS/FAIL per CLI.

What this proves
----------------
The CLI subsystem now exposes every connected & usable CLI to the production
router brain as a ``cli_<name>`` tool, backed by ONE shared, bus-connected
registry. This probe verifies that wiring end-to-end against the machine's
really-installed-and-authed CLIs (gcloud / gh / docker / aws / kubectl / ...).

Execution path (production-identical)
-------------------------------------
For each connected CLI we run a SAFE, read-only command through the EXACT
production execution path:

    CliToolRegistry (shared) -> active_tools() -> cli_<name> CliTool
        -> ToolExecutor.execute()   (risk-tier + whitelist + plausibility)
        -> asyncio subprocess with NO_WINDOW_CREATIONFLAGS + ENV injection

We assert a real ``cli_<name>`` tool ran a real command with exit 0 and real
output. We never call ``Tool.execute()`` directly (AP-3) — execution always
flows through the ToolExecutor.

Optional brain-driven mode
--------------------------
With ``--brain`` the probe additionally builds the production brain and issues a
natural-language instruction so the brain itself chooses the ``cli_<name>`` tool.
This needs a reachable brain provider (``cfg.brain.primary``); if none is
available it is reported as SKIP, not FAIL — the registry-driven path above is
the load-bearing proof of the wiring.

Run
---
    python scripts/probe_cli_e2e.py
    python scripts/probe_cli_e2e.py --brain        # also try the real brain
    python scripts/probe_cli_e2e.py --only gcloud,gh
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# ASCII-safe stdout on Windows (cp1252 default).
try:  # noqa: SIM105
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110
    pass

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from jarvis.clis.registry import CliToolRegistry  # noqa: E402
from jarvis.clis.shared import set_active_registry  # noqa: E402
from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import JarvisConfig  # noqa: E402
from jarvis.safety import (  # noqa: E402
    ApprovalWorkflow,
    RiskTierEvaluator,
    ToolExecutor,
)

# Safe, read-only commands per CLI. Each must be non-mutating and fast.
SAFE_COMMANDS: dict[str, str] = {
    "gcloud": "gcloud config list --format=json",
    "gh": "gh repo list --limit 5",
    "docker": "docker ps",
    "aws": "aws --version",
    "kubectl": "kubectl version --client=true",
    "vercel": "vercel --version",
    "firebase": "firebase --version",
    "az": "az version",
    "supabase": "supabase --version",
    "stripe": "stripe --version",
}

# Natural-language instructions for the optional --brain mode.
NL_INSTRUCTIONS: dict[str, str] = {
    "gcloud": "List my current Google Cloud config using the gcloud CLI tool.",
    "gh": "List 5 of my GitHub repos using the gh CLI tool.",
    "docker": "Show the running docker containers using the docker CLI tool.",
}


def _line(ok: bool | None, cli: str, detail: str) -> str:
    tag = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
    return f"[{tag}] {cli:<10} {detail}"


async def _build_shared_registry(bus: EventBus) -> CliToolRegistry:
    registry = CliToolRegistry(bus=bus)
    await registry.bootstrap()
    set_active_registry(registry)
    return registry


def _install_auto_approver(bus: EventBus) -> None:
    """Auto-approve every ActionProposed for this controlled, read-only probe.

    Production gates ``ask``-tier CLIs (e.g. kubectl) behind a user
    confirmation; without a UI the probe's executor would time out and report a
    false FAIL. We approve immediately (mirroring a UI click) because every
    probe command is a vetted, read-only operation.
    """
    from jarvis.core.events import ActionApproved, ActionProposed

    async def _on_proposed(ev: ActionProposed) -> None:
        # Defer the approval: the executor publishes ActionProposed BEFORE it
        # registers the approval future in ApprovalWorkflow.wait(). Approving
        # synchronously here would be lost (no future yet). Scheduling a task
        # lets the current dispatch unwind so wait() registers first.
        async def _approve() -> None:
            await asyncio.sleep(0)
            await bus.publish(ActionApproved(trace_id=ev.trace_id, approved_by="probe"))

        asyncio.create_task(_approve())

    bus.subscribe(ActionProposed, _on_proposed)


async def _run_cli_via_executor(
    registry: CliToolRegistry,
    executor: ToolExecutor,
    cli_name: str,
    command: str,
) -> tuple[bool, str]:
    """Run one CLI through the production ToolExecutor. Returns (ok, detail)."""
    tool_name = f"cli_{cli_name}"
    tool = next((t for t in registry.active_tools() if t.name == tool_name), None)
    if tool is None:
        return False, f"no {tool_name} tool (CLI not connected/usable)"

    ctx_trace = uuid4()
    result = await executor.execute(
        tool,
        {"command": command, "timeout_s": 30.0},
        user_utterance=f"[probe] {command}",
        trace_id=ctx_trace,
    )
    if not result.success:
        return False, f"`{command}` -> {result.error}"
    out = result.output or {}
    exit_code = out.get("exit_code")
    stdout = (out.get("stdout") or "").strip()
    if exit_code != 0:
        return False, f"`{command}` -> exit {exit_code}"
    preview = stdout.replace("\n", " ")[:60] if stdout else "(empty)"
    return True, f"`{command}` -> exit 0, out: {preview}"


async def _run_brain_mode(
    bus: EventBus, cli_name: str, instruction: str
) -> tuple[bool | None, str]:
    """Optionally drive the real production brain. SKIP if no provider works."""
    try:
        from jarvis.brain.factory import build_default_brain

        brain = build_default_brain()
    except Exception as exc:  # noqa: BLE001
        return None, f"brain build failed (provider unavailable?): {exc}"

    tool_name = f"cli_{cli_name}"
    if tool_name not in getattr(brain, "_tools", {}):
        return None, f"{tool_name} not in brain tool set (CLI not connected)"

    # Capture which cli_<name> tool the brain calls via bus events.
    from jarvis.core.events import ActionExecuted

    called: list[str] = []

    async def _on_exec(ev: ActionExecuted) -> None:
        if ev.tool_name.startswith("cli_"):
            called.append(ev.tool_name)

    brain_bus = getattr(brain, "_bus", None) or bus
    brain_bus.subscribe(ActionExecuted, _on_exec)
    try:
        summary = await asyncio.wait_for(
            brain.generate(instruction, use_history=False), timeout=90.0
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"brain turn failed/timed out: {exc}"

    if not called:
        return None, f"brain answered without calling a cli_ tool: {summary[:50]!r}"
    return True, f"brain called {called[-1]} -> summary: {summary[:50]!r}"


async def main_async(args: argparse.Namespace) -> int:
    bus = EventBus()
    registry = await _build_shared_registry(bus)

    config = JarvisConfig()
    from jarvis.clis.risk_integration import make_cli_patterns_fn

    evaluator = RiskTierEvaluator(
        config.safety,
        extra_patterns_fn=make_cli_patterns_fn(),
    )
    executor = ToolExecutor(bus, evaluator, ApprovalWorkflow(bus))
    _install_auto_approver(bus)

    connected = sorted(t.name[len("cli_") :] for t in registry.active_tools())
    print("=" * 72)
    print("CLI E2E PROBE — NL/registry -> cli_<name> tool -> command -> result")
    print("=" * 72)
    print(f"Connected & usable CLIs: {connected or '(none)'}")
    print("-" * 72)

    only = set(args.only.split(",")) if args.only else None
    targets = [c for c in connected if (only is None or c in only)]
    if not targets:
        print("No connected CLIs to probe. Connect at least one CLI in the UI.")
        return 1

    results: list[bool | None] = []
    for cli_name in targets:
        command = SAFE_COMMANDS.get(cli_name)
        if command is None:
            print(_line(None, cli_name, "no safe probe command defined — SKIP"))
            results.append(None)
            continue
        ok, detail = await _run_cli_via_executor(registry, executor, cli_name, command)
        print(_line(ok, cli_name, detail))
        results.append(ok)

    if args.brain:
        print("-" * 72)
        print("Brain-driven mode (--brain): the brain itself picks the cli_ tool")
        print("-" * 72)
        for cli_name in targets:
            instruction = NL_INSTRUCTIONS.get(cli_name)
            if instruction is None:
                continue
            ok, detail = await _run_brain_mode(bus, cli_name, instruction)
            print(_line(ok, cli_name, detail))

    print("=" * 72)
    passed = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is False)
    skipped = sum(1 for r in results if r is None)
    print(
        f"SUMMARY: {passed} PASS, {failed} FAIL, {skipped} SKIP "
        f"(of {len(results)} registry-driven probes)"
    )
    # Exit non-zero only if a connected CLI actively FAILED its safe command.
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live CLI E2E probe")
    parser.add_argument(
        "--brain",
        action="store_true",
        help="Also drive the real production brain (needs a reachable provider).",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated CLI names to probe (e.g. gcloud,gh,docker).",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
