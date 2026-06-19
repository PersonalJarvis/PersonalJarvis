"""Live A/B for the planner navigation-discipline fix (planner-only, no clicks).

Drives the REAL production planner path (`_make_plan` -> `_call_brain` -> the
live Gemini fast brain) with a real desktop screenshot, for the exact voice goal
that failed on 2026-06-15 20:54. It runs the planner TWICE on the same goal +
screenshot:

  * OLD = the planner prompt WITHOUT the navigation-vs-search discipline,
  * NEW = the current planner prompt (with the discipline),

and reports whether each plan injects a spurious literal keyword-search step
("type 'news'" / a search-box step) — the step that derailed the live run.

It NEVER executes an action: it only asks the brain for a plan. Safe on the
live desktop (it just captures one screenshot). ``os._exit`` at the end dodges
the known google-genai gRPC zombie-thread hang on interpreter shutdown.

Run:  python scripts/verify_planner_navigation_discipline.py
"""
from __future__ import annotations

import contextlib
import os
import sys

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import re

GOAL = "open chrome and search for the news post from Elon Musk on X"

# A plan step is a "spurious literal keyword search" if it types one of the
# goal's descriptor words into a search box (the recorded derailment).
_SEARCH_STEP_RE = re.compile(
    r"\b(search|type|enter|query)\b.*\b(news|latest|post|tweet)\b"
    r"|\b(news|latest|post|tweet)\b.*\b(search box|search field|search bar)\b",
    re.I,
)


def _classify(plan: list[dict[str, str]]) -> tuple[bool, list[str]]:
    """Return (has_spurious_search, rendered_steps)."""
    rendered = []
    spurious = False
    for i, step in enumerate(plan, 1):
        intent = step.get("intent", "")
        rendered.append(f"   {i}. {intent}  (success: {step.get('success', '')[:60]})")
        if _SEARCH_STEP_RE.search(intent):
            spurious = True
    return spurious, rendered


async def main() -> int:
    import jarvis.harness.screenshot_only_loop as loop_mod
    from jarvis.core.bus import EventBus
    from jarvis.harness.screenshot_only_loop import _make_plan

    print("=" * 72)
    print("LIVE PLANNER A/B — navigation-vs-search discipline")
    print(f"GOAL: {GOAL!r}")
    print("=" * 72)

    bus = EventBus()
    from jarvis.brain.factory import build_default_brain
    build_default_brain(bus=bus)
    from jarvis.harness.computer_use_context import get_computer_use_context
    ctx = get_computer_use_context()
    print(f"[setup] brain wired; verify={ctx.verify_after_each_step}")

    obs = await ctx.vision_engine.observe(mode="screenshot")
    print(f"[setup] captured screenshot hash={(obs.screenshot_hash or '?')[:16]}")

    new_prompt = loop_mod._PLANNER_SYSTEM_PROMPT
    if "* NAVIGATION vs SEARCH" not in new_prompt:
        print("[ABORT] the fix is not in the loaded code — NAVIGATION block missing")
        return 3
    old_prompt = new_prompt[: new_prompt.index("* NAVIGATION vs SEARCH")]

    async def plan_with(prompt: str) -> list[dict[str, str]]:
        loop_mod._PLANNER_SYSTEM_PROMPT = prompt
        try:
            return await _make_plan(ctx, observation=obs, user_goal=GOAL)
        finally:
            loop_mod._PLANNER_SYSTEM_PROMPT = new_prompt

    print("\n--- OLD prompt (no discipline) ---")
    old_plan = await plan_with(old_prompt)
    old_spurious, old_render = _classify(old_plan)
    print("\n".join(old_render) or "   (empty plan)")
    print(f"   => spurious literal-keyword search step present: {old_spurious}")

    print("\n--- NEW prompt (with discipline) ---")
    new_plan = await plan_with(new_prompt)
    new_spurious, new_render = _classify(new_plan)
    print("\n".join(new_render) or "   (empty plan)")
    print(f"   => spurious literal-keyword search step present: {new_spurious}")

    print("\n" + "=" * 72)
    if new_plan and not new_spurious:
        print("VERDICT: PASS — the NEW plan navigates to the target without a "
              "spurious 'news' keyword search.")
        if old_spurious and not new_spurious:
            print("        (and it flipped the recorded failure: OLD injected the "
                  "search step, NEW does not.)")
        rc = 0
    else:
        print("VERDICT: INCONCLUSIVE — NEW plan still contains a search step "
              "(LLM non-determinism; inspect above).")
        rc = 1
    print("=" * 72)
    return rc


if __name__ == "__main__":
    code = 1
    try:
        code = asyncio.run(asyncio.wait_for(main(), timeout=120))
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {type(exc).__name__}: {exc}")
    sys.stdout.flush()
    os._exit(code)  # hard exit: google-genai leaks non-daemon gRPC threads
