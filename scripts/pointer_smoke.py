"""AI Pointer smoke test — verify the deictic gate + cursor-element resolution live.

Run with the Jarvis Python (the interpreter the app actually uses). On Windows:

    & "C:\\Program Files\\Python311\\python.exe" scripts/pointer_smoke.py

It prints, against your CURRENT mouse position:
  1. the deictic gate decisions (which utterances trigger the pointer),
  2. the element resolved under the cursor via the OS accessibility tree,
  3. whether the crop fallback fired (only for unlabeled graphics),
  4. the prompt block the brain would receive,
  5. the per-turn push decision for a deictic vs an unrelated utterance.

No app restart needed — this exercises the feature modules directly. Move the
mouse over different things (a button, a graphic, a text field) and re-run to see
the resolution change. On a headless host (no cursor) everything degrades to a
graceful "not available" with no crash.
"""

from __future__ import annotations

import asyncio
import sys

try:  # cp1252-safe output on a Windows console (CLAUDE.md Windows note)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from jarvis.pointer.context import resolve_pointer_context  # noqa: E402
from jarvis.pointer.intent import is_pointing_intent  # noqa: E402
from jarvis.pointer.turn import resolve_turn_pointer  # noqa: E402

_GATE_CASES = [
    ("was ist das da?", True),
    ("was ist das hier?", True),
    ("worauf zeige ich gerade?", True),
    ("was ist das fuer ein Wetter?", False),  # veto: demonstrative + noun
    ("erzaehl mir einen Witz", False),
]


def main() -> None:
    print("=== 1. Deictic gate (expected | actual | utterance) ===")
    for text, expected in _GATE_CASES:
        actual = is_pointing_intent(text)
        mark = "OK " if actual == expected else "!! "
        print(f"  {mark} {expected!s:5} {actual!s:5} {text}")

    print("\n=== 2. Element under your CURRENT cursor (accessibility tree) ===")
    pc = resolve_pointer_context()
    print(f"  available={pc.available}  reason={pc.reason!r}  pos=({pc.x}, {pc.y})")
    if pc.element is not None:
        e = pc.element
        print(
            f"  name={e.name!r} role={e.role!r} labeled={e.is_labeled} "
            f"value={e.value[:50]!r} app={e.app_name!r} window={e.window_title!r}"
        )
    crop_len = len(pc.crop.data_b64) if pc.crop else 0
    print(f"  crop fallback fired: {pc.crop is not None}  ({crop_len} b64 chars)")

    print("\n=== 3. Prompt block the brain would receive ===")
    print(pc.render() or "  (nothing - not available)")

    print("\n=== 4. Per-turn push decision (gate + resolve in one call) ===")
    d_block, d_crop = asyncio.run(resolve_turn_pointer("was ist das da?", enabled=True))
    w_block, w_crop = asyncio.run(resolve_turn_pointer("wie ist das Wetter?", enabled=True))
    print(f"  deictic 'was ist das da?'  -> block={bool(d_block)} crop={d_crop is not None}")
    print(f"  unrelated 'wie ist das W.' -> block={bool(w_block)} crop={w_crop is not None}"
          "   (both must be False = no context-less garbage)")


if __name__ == "__main__":
    main()
