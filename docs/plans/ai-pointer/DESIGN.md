# AI Pointer — Design

Status: **implemented + verified** (2026-06-01) · Platforms: Windows / macOS / Linux (cloud-first compliant)

## Verification status (honest, per SIGNOFF-LOG convention)

- **CI-provable, GREEN:** intent gate, `PointerElement`, cursor backend factory +
  null fallback, region-crop math + encode, element-at-point resolver wrappers
  (fakes), context resolver + timeout, `inspect-pointer` tool, ROUTER_TOOLS
  wiring, deictic push into `BrainManager.generate()`, `[pointer]` config — all
  green on the full Jarvis Python (266 in the feature + touched-area sweep) and
  graceful-skip on the cloud-first base (no extras). `ruff` clean; `mypy` clean
  modulo baseline missing-stub notes for optional native packages.
- **Windows live, VERIFIED:** native UIA `ElementFromPoint` resolves real
  elements (named button, unlabeled image → crop fallback, document with value);
  full pipeline (gate → cursor → UIA → render → turn-push) verified 3× stable.
- **macOS / Linux element-at-point:** implemented + fake-tested,
  `unverified-on-real-desktop` until an operator runs it (AX / AT-SPI). Never
  claimed as live-verified.

---

Original status: **proposed** · Date: 2026-06-01 · Platforms: Windows / macOS / Linux (cloud-first compliant)

## 1. Goal

Jarvis understands **what the user's mouse cursor is pointing at** and answers
deictic questions ("what is *this*?", "was ist *das da*?") about the on-screen
element under the cursor — primarily via the **OS accessibility tree**, not by
guessing from a full-screen screenshot, and **only** when the utterance signals
a pointing intent. It must never inject cursor context into an unrelated turn
("how's the weather?" must not trigger a description of whatever happens to be
under the mouse).

## 2. Non-goals (YAGNI)

- **No always-on cursor feed.** Resolving the element is gated by intent and runs
  off the voice hot path (AP-9). No continuous polling, no background watcher.
- **Not the DeepMind "Astra" pointer.** No mouse-wiggle activation, no floating
  Gemini overlay. Our activation signal is the **deictic utterance + cursor
  position**, voice/chat-first.
- **Not a replacement** for permanent vision or computer-use; this is a focused,
  read-only "what is under my cursor right now" capability.

## 3. Core principle — "not just screenshots"

The **primary** signal is the **accessibility element under the cursor**:
name, control role, value/text, bounding box, owning app + window title —
obtained from a native *point query* (`ElementFromPoint` / `AXUIElementCopy​
ElementAtPosition` / `Component.getAccessibleAtPoint`). This is semantic, cheap,
and exact.

A **tight region crop** (a small square around the cursor) is captured **only as
augmentation** — chiefly when the element carries no accessible label (e.g. the
user points at a raster graphic). It is a region-of-interest, never a full-screen
dump. Both the element query and the crop are produced only on a gated turn.

This directly answers the user's requirement: "I don't want it to do this only
via screenshots — it has to do it some other way." The other way is the
accessibility tree; the crop is a scoped fallback for unlabeled pixels.

## 4. Architecture — units (each one purpose, testable in isolation)

| # | Unit | File | Responsibility | Depends on |
|---|------|------|----------------|------------|
| 1 | `PointerElement` | `jarvis/vision/pointer_types.py` | frozen dataclass: name, role, value, bounds, app_name, window_title, has_text, source | — |
| 2 | Intent gate | `jarvis/pointer/intent.py` | `is_pointing_intent(text) -> bool` — fast regex deictic detector + negative guard | config |
| 3 | Mouse backend | `jarvis/platform/mouse.py` | `MouseBackend` protocol + `make_mouse_backend()` (Win ctypes / Mac+Linux pynput / null) | platform seam |
| 4 | Element-at-point | `jarvis/vision/element_at_point.py` | `make_pointer_resolver()` → per-OS point query → `PointerElement \| None` | tree caps |
| 5 | Region crop | extend `jarvis/vision/screenshot.py` | `capture_region(bbox) -> bytes` (PIL crop around point) | mss/PIL |
| 6 | Context resolver | `jarvis/pointer/context.py` | `PointerContext` + `resolve_pointer_context()` — compose 3→4→(5) with a hard timeout; render a compact text block + optional `ImageBlock` | 1,3,4,5 |
| 7 | Pull tool | `jarvis/plugins/tool/inspect_pointer.py` | `inspect_pointer` router-tier tool (risk `safe`) for the chat path / explicit calls | 6 |
| 8 | Push injection | edit `jarvis/brain/manager.py` | on a gated turn, attach the pointer block to that single turn's prompt | 2,6 |
| 9 | Config | edit `jarvis/core/config.py` | `[pointer]` section: enabled, patterns, crop radius, timeout | — |

### Data flow (a gated turn)

```
utterance ──▶ is_pointing_intent? ──no──▶ normal turn (no pointer context)
                   │ yes
                   ▼
        resolve_pointer_context()  (off hot path, hard timeout ~250 ms)
                   │
     make_mouse_backend().position() ─▶ (x, y)
                   │
     make_pointer_resolver().at(x, y) ─▶ PointerElement | None
                   │   (if element unlabeled or graphic)
     capture_region(bbox(x,y,radius)) ─▶ tiny JPEG ─▶ ImageBlock
                   ▼
   PointerContext.render() ─▶ text block injected into THIS turn's prompt
                   ▼
            brain.generate() ─▶ answer ─▶ scrub_for_voice ─▶ TTS
```

## 5. The intent gate (the "no context-less garbage" contract)

Fire **only** on deictic/pointing references that are *not* completed by a
concrete noun:

- Positive: `das da`, `das hier`, `das dort`, `da drüben`, `dieses ding`,
  `worauf ich (gerade) zeige`, `wo ich hinzeige`, `was ist das` (when trailing),
  `what is this`, `this thing`, `right here`, `over here`, `point(ing) at`,
  pointing verbs `zeig*/deut*/hover*`.
- **Negative guard**: if a concrete noun immediately follows the demonstrative
  (`das Wetter`, `the weather`, `diese Datei` when a filename is named), the gate
  does **not** fire. Implemented as a "demonstrative + noun" veto regex evaluated
  before the positive match.

Patterns live under `[pointer]` in `jarvis.toml`, built with the same regex
builders used by `[brain.routing]` (`_build_verb_pattern` family). Pure function,
regex only — no LLM, microsecond cost, safe on the hot path's edge.

Belt-and-braces: the `inspect_pointer` **tool** lets the brain pull the context
explicitly when it is uncertain, and the gate only *adds* context — a false
positive yields an extra (ignored) sentence, never a wrong answer, because the
brain still reads the actual question.

## 6. Cloud-first / cross-platform compliance

- Every backend is **extras-gated** and degrades to a **logged null fallback**
  (AD-6). On a headless €5 VPS there is no cursor: `make_mouse_backend()` returns
  the null backend → `resolve_pointer_context()` returns "no pointer available"
  → the brain answers "I don't have a cursor to look at here." No crash, no
  required dependency. The base `python:3.11-slim` import still succeeds.
- `pynput` is already in the `[desktop]` extra; Windows uses stdlib `ctypes`.
  Windows UIA point query uses `comtypes`/`uiautomation` (Windows-only, lazy
  import inside the function body — HN-7). **No new hard dependency.**
- Capability probe `has_cursor` added to `jarvis/platform/probes.py`.

## 7. Verification honesty (per SIGNOFF-LOG convention)

- **CI-provable (all platforms):** intent gate, mouse factory + null fallback,
  region crop, context resolver (with fakes), tool execute, routing registration,
  headless import smoke.
- **Windows live:** element-at-point against a real window (we are on Windows).
- **macOS / Linux element-at-point:** implemented + unit-tested with fakes, but
  `unverified-on-real-desktop` until an operator runs it. Labelled honestly; never
  claimed as "live-verified".

## 8. Anti-patterns respected

- **AP-9** pointer resolution is off the voice critical path (gated turn + hard
  timeout; no continuous feed).
- **AP-3** tool runs only via `ToolExecutor.execute()`.
- **AP-11** no LLM call inside `scrub_for_voice`; the gate is regex-only.
- **AP-1** any subprocess (none expected) uses `NO_WINDOW_CREATIONFLAGS`.
- **Enum drift** `PointerElement.role` reuses the existing UIA role vocabulary —
  no new wire-format enum introduced.

## 9. Build sequence (back-engineered steps — execute in order, TDD)

1. `PointerElement` + intent gate (pure logic). Test deictic positives + the
   `das Wetter` negative.
2. `mouse.py` backend protocol + factory + `has_cursor` probe. Test null fallback.
3. `element_at_point.py` per-OS resolver + null fallback. Unit-test with fakes;
   Windows live smoke.
4. `capture_region(bbox)` in `screenshot.py`. Test crop math + clamping.
5. `PointerContext` + `resolve_pointer_context()` with timeout. Test compose +
   timeout + null path.
6. `inspect_pointer` tool + entry-point + `ROUTER_TOOLS` + `test_routing.py`.
   `pip install -e . --no-deps` so the entry-point is discoverable.
7. Deictic-gated push injection in `manager.generate()` / `_build_dispatcher`.
   Test: gated turn attaches block; ungated turn does not.
8. `[pointer]` config section (extra="allow"-safe, defaults load cleanly).
9. Verify: targeted pytest + cross-platform import smoke + `ruff` + `mypy`.

## 10. Open risks

- UIA `ElementFromPoint` can return a deep leaf with an empty Name; mitigate by
  walking up to the nearest named ancestor (bounded) before falling back to the
  crop.
- DPI scaling: cursor coords and UIA bounds are in physical pixels under
  per-monitor DPI awareness (already set by `screenshot._ensure_dpi_awareness`).
  Region crop must use the same coordinate space.
- Wayland: no global cursor position via pynput → null fallback (logged once).
