"""Smoke test: confirm the main Jarvis brain runs through Gemini 3.5 Flash.

Builds the production brain callback the way the voice pipeline does and
prints both the resolved config model IDs and the live reply.
"""
from __future__ import annotations

import asyncio
import sys
import time

from jarvis.core.config import load_config
from jarvis.brain.factory import build_default_brain


async def main() -> int:
    cfg = load_config()
    print(f"[cfg] jarvis pkg                = {sys.modules['jarvis'].__file__}")
    print(f"[cfg] brain.routing_model       = {cfg.brain.routing_model!r}")
    print(f"[cfg] brain.router.model        = {cfg.brain.router.model!r}")
    providers = cfg.brain.providers
    gemini = providers["gemini"] if isinstance(providers, dict) else providers.gemini
    gmodel = gemini["model"] if isinstance(gemini, dict) else gemini.model
    gdeep = (gemini.get("deep_model") if isinstance(gemini, dict) else getattr(gemini, "deep_model", None))
    print(f"[cfg] providers.gemini.model    = {gmodel!r}")
    print(f"[cfg] providers.gemini.deep_mdl = {gdeep!r}")

    brain = build_default_brain(tier="router", allow_phase2=True)
    prompt = "Reply in one short sentence and name the exact Gemini model id you are running on."
    print(f"\n[probe] prompt = {prompt!r}\n")

    t0 = time.perf_counter()
    reply = await brain(prompt)
    elapsed = (time.perf_counter() - t0) * 1000.0

    print(f"[reply]    {reply}")
    print(f"[elapsed]  {elapsed:.0f} ms")
    return 0 if reply else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
