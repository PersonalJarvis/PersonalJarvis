# Intelligent Vision Gating (Hybrid) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop attaching a screenshot to every conversational turn; attach only on a clear screen reference, and let the brain pull the screen on demand when it decides it needs to look.

**Architecture:** Two waves. Wave 1 inverts the `vision_gate` heuristic from attach-by-default to attach-only-on-visual-reference (pure, zero-latency). Wave 2 wires tool image artifacts back into the conversation so the existing `screenshot` tool becomes a real on-demand "look at the screen" capability — the safety net for screen references the heuristic misses (anti-regression vs. 2026-04-28 blank-desktop hallucination).

**Tech Stack:** Python 3.11, pytest (asyncio_mode=auto), regex heuristic, Gemini multimodal (`inline_data` on user-role only).

**Spec:** `docs/plans/vision-gating-hybrid.md`

---

## File Structure

- `jarvis/brain/vision_gate.py` — Wave 1: inverted decision + expanded markers (pure functions, ~60 lines).
- `tests/unit/brain/test_vision_gate.py` — Wave 1: pure-function unit tests (new).
- `tests/integration/test_conditional_vision.py` — Wave 1: existing wiring tests stay green; add the user's regression case.
- `jarvis/brain/tool_use_loop.py` — Wave 2: artifact→image feedback after a tool result.
- `tests/unit/brain/test_tool_use_loop_image_feedback.py` — Wave 2: image-feedback unit test (new).
- `jarvis/brain/router.py` — Wave 2: one prompt sentence about `look_at_screen`/screenshot.

---

## Wave 1 — Invert the heuristic

### Task 1: Pure-function unit tests for the inverted gate

**Files:**
- Test: `tests/unit/brain/test_vision_gate.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Wave 1: the vision gate attaches a screenshot ONLY on a clear screen reference."""
from __future__ import annotations

import pytest

from jarvis.brain.vision_gate import has_visual_marker, should_attach_screenshot

# Turns that clearly refer to the screen → attach.
_VISUAL = [
    "was siehst du hier",
    "schau mal das hier",
    "klick auf den Button", <!-- i18n-allow -->
    "warum ist das rot?", <!-- i18n-allow -->
    "lies mir die Fehlermeldung vor",
    "was steht da auf dem Bildschirm", <!-- i18n-allow -->
    "mach das Fenster zu",
    "look at this window",
]

# Turns that are conversational / factual → no screenshot, even though they are
# not "smalltalk". This is the user's reported case.
_NON_VISUAL = [
    "was haben wir gerade besprochen?",
    "erklär mir nochmal das Thema", <!-- i18n-allow -->
    "wie spät ist es", <!-- i18n-allow -->
    "was ist die Hauptstadt von Frankreich", <!-- i18n-allow -->
    "warum ist das so wichtig?",  # i18n-allow   # "warum ist das" is NOT a marker without a colour
    "fass das bitte zusammen",
]


@pytest.mark.parametrize("text", _VISUAL)
def test_visual_reference_attaches(text: str) -> None:
    assert should_attach_screenshot(text, is_smalltalk=False) is True
    assert should_attach_screenshot(text, is_smalltalk=True) is True


@pytest.mark.parametrize("text", _NON_VISUAL)
def test_non_visual_skips(text: str) -> None:
    # Inverted logic: a non-smalltalk content question no longer auto-attaches.
    assert should_attach_screenshot(text, is_smalltalk=False) is False
    assert should_attach_screenshot(text, is_smalltalk=True) is False


def test_has_visual_marker_examples() -> None:
    assert has_visual_marker("klick drauf") is True
    assert has_visual_marker("was haben wir besprochen") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/brain/test_vision_gate.py -v`
Expected: FAIL — `test_non_visual_skips` fails for "was haben wir gerade besprochen?" (current code returns True because `not is_smalltalk`).

- [ ] **Step 3: Rewrite `vision_gate.py` (inverted logic + expanded markers)**

Replace the whole file `jarvis/brain/vision_gate.py` with:

```python
"""Visual-reference vision gate (Hybrid — attach-only-on-reference).

The router runs text-only by default. A screenshot is attached ONLY when the
utterance clearly refers to the screen (deictic pointer, screen noun, look/click
verb, read-out/diagnosis). Inverted from the old skip-when-safe default, which
attached on every non-smalltalk turn and let a fresh screenshot dominate the
model's attention over the conversation history (user asked "what did we just
discuss?" and got a screen-based answer).

The on-demand screenshot tool (wired in tool_use_loop, Wave 2) is the safety net
for screen references the markers miss, so the router never goes blind on a real
screen question (anti-regression vs. the 2026-04-28 blank-desktop hallucination).
"""
from __future__ import annotations

import re

# Visual-reference markers (DE + EN). Substring matching is intentional
# ("klick" also catches "anklicken", "schau" catches "anschauen"). Markers are
# kept specific on purpose: a false negative is recoverable (the brain can call
# the screenshot tool), a false positive re-introduces the per-turn image tax
# this change exists to remove. Deliberately NOT included: bare "tab", "dort",
# "warum ist das" (without a colour), "was ist das" (without "hier") — too broad,
# they fire on non-visual turns.
_VISUAL_MARKERS: tuple[str, ...] = (
    # deictic / pointing
    "das hier", "das da", "hier auf", "da auf", "hier oben", "hier unten", <!-- i18n-allow -->
    "hier links", "hier rechts", "hier im", "hier in der",
    # look / show verbs
    "schau", "sieh", "siehst", "guck", "zeig mir", "zeig mal",
    # screen / window / page nouns
    "auf dem bildschirm", "am bildschirm", "im bild", "auf dem screen", <!-- i18n-allow -->
    "bildschirm", "dieses fenster", "das fenster", "diese seite",
    "die seite hier", "fehlermeldung", "knopf", "button", "menü", "menue", <!-- i18n-allow -->
    "dialog",
    # actions on the screen
    "klick", "markier", "scroll", "öffne das", "oeffne das", "mach das zu", <!-- i18n-allow -->
    "mach das fenster", "schließ das fenster", "schliess das fenster", <!-- i18n-allow -->
    # diagnosis / read-out
    "warum ist das rot", "warum ist das grau", "warum ist das blau", <!-- i18n-allow -->
    "was steht da", "was steht hier", "was ist das hier", "lies vor", <!-- i18n-allow -->
    "lies mir das", "lies das", "vorlesen",
    # English
    "this here", "that there", "look at", "see this", "on screen",
    "on the screen", "this window", "what's this", "what is this", "click",
    "the screen", "read this", "this error", "this button", "this page",
)

_MARKER_RE = re.compile("|".join(re.escape(m) for m in _VISUAL_MARKERS), re.IGNORECASE)


def has_visual_marker(text: str) -> bool:
    """True if the utterance contains a deictic / visual-reference marker."""
    return bool(_MARKER_RE.search(text or ""))


def should_attach_screenshot(text: str, *, is_smalltalk: bool = False) -> bool:
    """Decide whether to attach the screenshot for this turn (attach-on-reference).

    Returns True ONLY when the utterance clearly refers to the screen. A plain
    content question — even a non-smalltalk one — gets NO screenshot, so the
    conversation history stays the model's primary context. The on-demand
    screenshot tool is the fallback for references the markers miss.

    ``is_smalltalk`` is accepted for backward compatibility with the existing
    call site but no longer forces attachment; the decision is the visual-marker
    signal alone.
    """
    return has_visual_marker(text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/brain/test_vision_gate.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Verify existing integration wiring still passes**

Run: `pytest tests/integration/test_conditional_vision.py -v`
Expected: PASS — "wie spät ist es" → `()`, "was siehst du hier" → 1 image ("siehst"), "schau mal das hier" → 1 image ("schau"). The inversion does not break these because the kept-image cases all carr <!-- i18n-allow -->y markers.

- [ ] **Step 6: Add the user's regression case to the integration test**

In `tests/integration/test_conditional_vision.py`, append:

```python
async def test_conversation_recall_skips_screenshot(tmp_path) -> None:
    """The reported bug: a 'what did we discuss?' turn must NOT attach a screenshot,
    so the conversation history is the brain's context, not the current screen."""
    m = _manager(_make_obs(tmp_path))
    imgs = await m._collect_vision_images(
        trace_id=uuid4(), user_text="was haben wir gerade besprochen?", is_smalltalk=False
    )
    assert imgs == ()
```

- [ ] **Step 7: Run the new integration case**

Run: `pytest tests/integration/test_conditional_vision.py::test_conversation_recall_skips_screenshot -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add jarvis/brain/vision_gate.py tests/unit/brain/test_vision_gate.py tests/integration/test_conditional_vision.py
git commit -m "feat(vision): attach screenshot only on a clear screen reference (Wave 1)"
```

---

## Wave 2 — Image-as-a-tool (on-demand safety net)

### Task 2: Feed tool image artifacts back into the conversation

**Files:**
- Modify: `jarvis/brain/tool_use_loop.py` (after the tool-result message append, inside the tool-call loop)
- Test: `tests/unit/brain/test_tool_use_loop_image_feedback.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Wave 2: a tool that returns an image artifact feeds it back as a user-role
message carrying an ImageBlock, so a vision provider can see it next iteration."""
from __future__ import annotations

from jarvis.brain.tool_use_loop import _images_from_artifacts
from jarvis.core.protocols import ImageBlock


def test_image_artifact_becomes_image_block() -> None:
    arts = ({"type": "image", "mime": "image/jpeg", "data": "QUJD"},)
    blocks = _images_from_artifacts(arts)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ImageBlock)
    assert blocks[0].mime == "image/jpeg"
    assert blocks[0].data_b64 == "QUJD"


def test_text_only_artifacts_yield_no_images() -> None:
    assert _images_from_artifacts(()) == []
    assert _images_from_artifacts(("some text note",)) == []
    assert _images_from_artifacts(({"type": "image"},)) == []  # no data → skip
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/brain/test_tool_use_loop_image_feedback.py -v`
Expected: FAIL — `ImportError: cannot import name '_images_from_artifacts'`.

- [ ] **Step 3: Add the helper + wire it in `tool_use_loop.py`**

Add this module-level helper near the top of `jarvis/brain/tool_use_loop.py` (after imports; ensure `ImageBlock` is imported from `jarvis.core.protocols`):

```python
def _images_from_artifacts(artifacts: object) -> list["ImageBlock"]:
    """Extract ImageBlocks from a tool's artifacts (Wave 2 on-demand vision).

    A vision-capable tool (e.g. the screenshot tool) returns
    ``artifacts=({"type": "image", "mime": ..., "data": <base64>},)``. We turn
    each image artifact into an ImageBlock so it can ride on a user-role message
    back into the conversation. Non-image / malformed artifacts are skipped.
    """
    blocks: list[ImageBlock] = []
    for art in artifacts or ():
        if isinstance(art, dict) and art.get("type") == "image" and art.get("data"):
            blocks.append(ImageBlock(
                mime=str(art.get("mime") or "image/jpeg"),
                data_b64=str(art["data"]),
            ))
    return blocks
```

Then, inside the tool-call loop, immediately AFTER the existing tool-result
message append (the `current_messages.append(BrainMessage(role="tool", ...))`
block, currently ending around line 534), add the image feedback — guarded so it
only runs when the tool actually executed and returned image artifacts:

```python
                # Wave 2 (vision-on-demand): if the tool returned image
                # artifact(s), feed them back as a user-role message so a
                # vision-capable provider can see them on the next iteration.
                # tool-role becomes a Gemini functionResponse (no image support),
                # so the image MUST ride on a user message. Gated on a real
                # execution (the refusal/guard branches set no ``result``).
                _exec_result = locals().get("result")
                if _exec_result is not None:
                    _img_blocks = _images_from_artifacts(
                        getattr(_exec_result, "artifacts", ()) or ()
                    )
                    if _img_blocks:
                        current_messages.append(BrainMessage(
                            role="user",
                            content="(Screenshot vom Tool — bitte beschreiben / nutzen.)",
                            images=tuple(_img_blocks),
                        ))
```

> Implementation note: at apply time, confirm `result` is the variable bound in
> the execution `else`-branch (line ~505) and that `result = None` is reset at
> the top of each tool-call iteration; if it is not reset, initialise
> `result = None` before the `if/elif/else` chain so a guard branch on a later
> iteration can't reuse a stale `result`. The `locals().get("result")` guard
> above is defensive but the explicit reset is cleaner — prefer it.

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `pytest tests/unit/brain/test_tool_use_loop_image_feedback.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full tool-use-loop + routing suites (no regressions)**

Run: `pytest tests/unit/brain/ -v`
Expected: PASS (no existing tool-use-loop test broken by the added append).

- [ ] **Step 6: Commit**

```bash
git add jarvis/brain/tool_use_loop.py tests/unit/brain/test_tool_use_loop_image_feedback.py
git commit -m "feat(vision): feed tool image artifacts back into the conversation (Wave 2)"
```

### Task 3: Prompt the router to use the screenshot tool on demand

**Files:**
- Modify: `jarvis/brain/router.py` (SYSTEM_PROMPT, lines 55-294)

- [ ] **Step 1: Read the SYSTEM_PROMPT and locate the tool-guidance area**

Run: `grep -n "screenshot\|screen-snapshot\|Bildschirm\|Tools" jarvis/brain/router.py | head`
Confirm where tool usage is described.

- [ ] **Step 2: Add one guidance sentence**

Insert into the SYSTEM_PROMPT (tool-guidance section) a single sentence, English source per output policy is NOT required here because router.py prompt is German user-facing instruction text — match the surrounding language of SYSTEM_PROMPT. Add (matching existing prompt language):

```
Wenn du unsicher bist, ob du den aktuellen Bildschirm sehen musst, um eine Frage zu beantworten, rufe das Tool `screenshot` auf — du bekommst kein Bild automatisch, sondern nur wenn du danach fragst o <!-- i18n-allow -->der der Nutzer klar auf den Bildschirm verweist.
```

- [ ] **Step 3: Verify the screenshot tool is reachable by the router**

Run: `python -c "from jarvis.brain.factory import ROUTER_TOOLS; print('screen-snapshot' in ROUTER_TOOLS)"`
Expected: `True` (already confirmed; this is a regression guard).

- [ ] **Step 4: Commit**

```bash
git add jarvis/brain/router.py
git commit -m "feat(vision): tell the router it can pull the screen on demand (Wave 2)"
```

---

## Final verification

- [ ] Run the focused suites:
  `pytest tests/unit/brain/test_vision_gate.py tests/integration/test_conditional_vision.py tests/unit/brain/test_tool_use_loop_image_feedback.py -v`
  Expected: all PASS.
- [ ] Run the broader brain suite for regressions:
  `pytest tests/unit/brain/ -v`
- [ ] Manual (after app restart): voice session — "merk dir die Zahl 12" → "und 5 dazu" → "welche Zahl hatten wir?" answers from conversation, no screen fixation; "was siehst du auf dem Bildschirm?" s <!-- i18n-allow -->till attaches the image; "warum ist das rot?" attaches; a vague screen question with no marker triggers a `screenshot` tool call instead of going blind.
