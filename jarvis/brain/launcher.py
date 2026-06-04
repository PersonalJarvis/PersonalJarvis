"""Standalone CLI for brain-layer tests without the voice pipeline.

    python -m jarvis.brain.launcher --provider gemini --prompt "Hallo"
    python -m jarvis.brain.launcher --provider claude-api --prompt "Was ist 2+2?"
    python -m jarvis.brain.launcher --list-providers
    python -m jarvis.brain.launcher --prompt "öffne Notepad"              (with tools)
    python -m jarvis.brain.launcher --prompt "formatiere C:"             (blacklist deny)
    python -m jarvis.brain.launcher --prompt "merk dir: ich heiße Sam" --save-memory

The launcher bypasses the voice pipeline entirely. It is the primary
verification CLI for Phase 2 and remains permanently useful as a debug tool.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

# Windows: reconfigure stdout to UTF-8 so that umlauts work correctly.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis.brain.launcher",
        description="Standalone-CLI für Brain-Layer-Tests",
    )
    p.add_argument("--prompt", type=str, help="User-Prompt an das Brain")
    p.add_argument("--provider", type=str, help="Primary-Provider überschreiben")
    p.add_argument("--list-providers", action="store_true",
                   help="Listet alle verfügbaren/geladenen Providers")
    p.add_argument("--snapshot", action="store_true",
                   help="Gibt BrainManager.snapshot() aus")
    p.add_argument("--with-tools", action="store_true",
                   help="Registriert alle 5 Phase-2-Tools im Dispatcher")
    p.add_argument("--no-memory", action="store_true",
                   help="Kein Core-Memory + Recall laden (für Minimal-Tests)")
    p.add_argument("--stream", action="store_true",
                   help="Streamt Token-weise auf stdout statt final-print")
    return p


async def _run(args: argparse.Namespace) -> int:
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import DATA_DIR, load_config

    # 1. List-providers
    if args.list_providers:
        from jarvis.brain.provider_registry import BrainProviderRegistry
        reg = BrainProviderRegistry()
        print("Verfügbare Brain-Providers:")
        for name in reg.available():
            print(f"  - {name}")
        failed = reg.failed()
        if failed:
            print("")
            print("Fehlgeschlagene Imports:")
            for name, err in failed.items():
                print(f"  - {name}: {err}")
        return 0

    config = load_config()
    if args.provider:
        # Primary override
        config.brain.primary = args.provider

    bus = EventBus()

    # Memory setup
    core_memory = None
    recall = None
    if not args.no_memory:
        from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory, MessageRecorder, RecallStore
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        core_memory = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
        recall = RecallStore(DATA_DIR / "jarvis.db")
        await recall.open()
        MessageRecorder(recall).attach(bus)

    # Tools and safety
    tools: dict = {}
    tool_executor = None
    if args.with_tools:
        from jarvis.clis.risk_integration import make_cli_patterns_fn
        from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
        evaluator = RiskTierEvaluator(
            config.safety, extra_patterns_fn=make_cli_patterns_fn(),
        )
        approval = ApprovalWorkflow(bus)
        tool_executor = ToolExecutor(bus, evaluator, approval)
        tools = _load_tools()

    manager = BrainManager(
        config=config,
        bus=bus,
        core_memory=core_memory,
        recall=recall,
        tools=tools,
        tool_executor=tool_executor,
    )

    if args.snapshot:
        import json as _json
        print(_json.dumps(manager.snapshot(), ensure_ascii=False, indent=2, default=str))
        return 0

    if not args.prompt:
        print("Kein --prompt angegeben. Benutze --help.", file=sys.stderr)
        return 2

    # Send prompt
    print(f"[provider] {manager.active_provider}")
    print(f"[prompt]   {args.prompt}")
    print("[response]")
    if args.stream:
        async for chunk in manager.dispatcher.stream_text(args.prompt):
            print(chunk, end="", flush=True)
        print()  # newline
    else:
        response = await manager.generate(args.prompt)
        print(response)

    if recall is not None:
        await recall.close()
    return 0


def _load_tools() -> dict:
    """Loads all tools from the entry points.

    Supports the **virtual-loader pattern**: if the instantiated class carries
    the attribute ``is_virtual_loader=True``, its ``expand()`` method is called
    instead of using the instance directly. ``expand()`` returns a list of tool
    instances, which allows dynamically generated tools (e.g. the CLI-tool
    integration that needs one tool per connected CLI) to work despite static
    entry points.
    """
    from importlib.metadata import entry_points
    tools: dict = {}
    for ep in entry_points(group="jarvis.tool"):
        try:
            cls = ep.load()
            inst = cls()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Tool '{ep.name}' nicht ladbar: {exc}", file=sys.stderr)
            continue
        if getattr(inst, "is_virtual_loader", False):
            try:
                expanded = inst.expand()
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] virtual-loader '{ep.name}' expand() failed: {exc}",
                      file=sys.stderr)
                continue
            for tool in expanded:
                tools[tool.name] = tool
        else:
            tools[inst.name] = inst
    if "hotkey" not in tools:
        try:
            from jarvis.plugins.tool.hotkey import HotkeyTool

            inst = HotkeyTool()
            tools[inst.name] = inst
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Fallback-Tool 'hotkey' nicht ladbar: {exc}", file=sys.stderr)
    if "click" not in tools:
        try:
            from jarvis.plugins.tool.click import ClickTool

            inst = ClickTool()
            tools[inst.name] = inst
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Fallback-Tool 'click' nicht ladbar: {exc}", file=sys.stderr)
    return tools


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
