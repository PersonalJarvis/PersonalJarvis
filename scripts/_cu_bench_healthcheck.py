"""One-shot pre-flight for the CU benchmark: build the brain, check the harness
is healthy, and report which vision-capable brain the fallback chain would use
and the active latency knobs — WITHOUT touching the desktop. Throwaway helper."""
from __future__ import annotations

import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main() -> int:
    from jarvis.core.bus import EventBus
    bus = EventBus()
    from jarvis.brain.factory import build_default_brain
    build_default_brain(bus=bus)

    from jarvis.harness.computer_use_context import get_computer_use_context
    from jarvis.plugins.harness.computer_use import ComputerUseHarness

    try:
        ctx = get_computer_use_context()
    except Exception as exc:
        print(f"ABORT: ComputerUseContext not wired: {exc}")
        return 3

    print("KNOBS:",
          "image_max_dimension=", getattr(ctx, "image_max_dimension", "?"),
          "image_max_bytes=", getattr(ctx, "image_max_bytes", "?"),
          "settle_scale=", getattr(ctx, "settle_scale", "?"),
          "think_timeout_cap_s=", getattr(ctx, "think_timeout_cap_s", "?"))

    mgr = ctx.brain_manager
    # Which provider chain would CU's fast tier use, and which are vision-capable?
    chain = []
    build_chain = getattr(mgr, "_build_fallback_chain", None)
    if callable(build_chain):
        try:
            chain = list(build_chain("fast") or [])
        except Exception as exc:
            print("chain build failed:", exc)
    print("FAST CHAIN:", chain)
    for provider, model in chain:
        try:
            brain = mgr._get_brain(provider, model)
            sv = getattr(brain, "supports_vision", None)
            print(f"  - {provider}({model}) supports_vision={sv}")
        except Exception as exc:
            print(f"  - {provider}({model}) build FAILED: {type(exc).__name__}: {exc}")
    dead = getattr(mgr, "_dead_providers", None)
    print("DEAD PROVIDERS:", dead)

    harness = ComputerUseHarness()
    healthy = await harness.health()
    print("HARNESS HEALTHY:", healthy)
    return 0 if healthy else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
