"""Standalone benchmark: 3x back-to-back calls to openclaw."""
import asyncio
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "warm_keep_bench.log"


async def main():
    from jarvis.core.protocols import BrainMessage, BrainRequest
    from jarvis.plugins.brain.openclaw_sdk import OpenClawSDKBrain

    brain = OpenClawSDKBrain(model="haiku")
    lines: list[str] = []

    async def once(txt: str) -> tuple[float, str]:
        req = BrainRequest(messages=(BrainMessage(role="user", content=txt),), max_tokens=64)
        t0 = time.perf_counter()
        out = ""
        async for d in brain.complete(req):
            if d.content:
                out += d.content
            if d.finish_reason:
                break
        return time.perf_counter() - t0, out

    try:
        for i, q in enumerate(["Sag Hi.", "Sag Hallo.", "Sag Servus."], 1):
            try:
                dt, r = await asyncio.wait_for(once(q), timeout=60)
                msg = f"Call {i}: {dt:.2f}s -> {r[:80]!r}"
            except Exception as exc:
                msg = f"Call {i}: ERROR {type(exc).__name__}: {exc}"
            print(msg, flush=True)
            lines.append(msg)
            OUT.write_text("\n".join(lines) + "\nin-progress\n", encoding="utf-8")
    finally:
        try:
            await brain.close()
        except Exception:
            pass
        lines.append("DONE")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("DONE", flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    asyncio.run(main())
