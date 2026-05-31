"""Screenshot-Only Computer-Use Loop (POAV simplification, 2026-05-26).

One screenshot -> one LLM call -> ONE OR MORE actions per step (the latter
when the model returns a batch; see below). No Set-of-Marks, no UIA tree,
no replan budget, no verify-after-step pass.

Action schema (the model returns either a single object OR a list of
objects per turn -- the executor handles both):

    {"action": "click_element", "name": "<UIA label>"}   UIA-grounded click
    {"action": "click",         "x": <int>, "y": <int>}  0-1000 normalized coords
    {"action": "type",          "text": "<string>"}      type into focused field
    {"action": "open_app",      "name": "<app name>"}    launch an app by name
    {"action": "wait",          "ms": <int 0-10000>}     in-loop pause, no LLM
    {"action": "done"}                                   user goal achieved
    {"action": "fail",          "reason": "<string>"}    impossible from here

The system prompt instructs the model to ALWAYS prefer click_element over
raw click when the target has a readable label, because Vision LLMs cannot
reliably ground raw pixel coordinates from a screenshot (live evidence
2026-05-27: Gemini Flash guessed (646, 984) / (36, 262) for the Calc "7"
button across 6 attempts and never landed it).

A LIST of actions is a "batch" / plan-then-execute step. The whole list
runs under ONE screenshot -- no fresh observe between items -- so e.g.
``[click, wait, click]`` is a single iteration. Used to amortise the
~1.7 s LLM round-trip across multiple actions when the current screenshot
shows every target. Max 6 actions per batch (truncated otherwise).

Termination paths:
    - "done"            -> exit_code 0
    - "fail"            -> exit_code 5
    - parse error       -> exit_code 2
    - step budget       -> exit_code 4
    - tool failure      -> exit_code 8
    - observe failure   -> exit_code 1
    - any timeout       -> exit_code 124
    - cancel token      -> exit_code 130

The Brain is dispatched directly through the active provider's fast-tier
brain (Gemini Flash when configured) -- not through the Router gate -- so
the screenshot is the sole grounding signal. The model NEVER receives a
UIA node listing or a Set-of-Marks legend.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from jarvis.core.events import ActionPlanned, ObservationCaptured
from jarvis.core.protocols import (
    BrainMessage,
    BrainRequest,
    CancelToken,
    HarnessResult,
    HarnessTask,
    ImageBlock,
    Observation,
)

if TYPE_CHECKING:
    from .computer_use_context import ComputerUseContext

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class CULoopError(RuntimeError):
    """Structural error in the screenshot-only loop."""


_VALID_ACTIONS: frozenset[str] = frozenset(
    {"click", "click_element", "type", "key", "scroll", "open_app", "wait", "done", "fail"}
)

#: Allowed scroll directions (mirror of ``ScrollTool._VALID_DIRECTIONS``).
_SCROLL_DIRECTIONS: frozenset[str] = frozenset({"up", "down", "left", "right"})

#: Default scroll magnitude (wheel notches) when the model omits ``amount`` —
#: enough to move a typical list/page by a usable chunk without overshooting.
_DEFAULT_SCROLL_AMOUNT: int = 3

# Hard cap on a single ``wait`` action so a hallucinated "wait 1 hour" cannot
# freeze the mission. 10 s is enough for any app-launch / page-load pause we
# realistically need; longer goals should re-screenshot and re-plan.
_MAX_WAIT_MS: int = 10_000

# Hard cap on the batch size returned in one model response. Limits the
# blast radius if the model returns a huge speculative plan that misses
# the actual UI state — at most ``_MAX_BATCH`` clicks happen blind before
# we re-screenshot and re-plan.
_MAX_BATCH: int = 6

# Exit codes — kept stable for callers that branch on them (voice/UI layer).
_TIMEOUT_EXIT_CODE = 124
_FAIL_EXIT_CODE = 5
_BUDGET_EXIT_CODE = 4
_PARSE_EXIT_CODE = 2
_TOOL_EXIT_CODE = 8
_OBSERVE_EXIT_CODE = 1
_CANCEL_EXIT_CODE = 130

# Defensive: strip ```json``` fences if a model ignores the no-fence rule.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


_SYSTEM_PROMPT = (
    "You are Jarvis' computer-use agent. Look at the screenshot and decide "
    "the next action(s) that advance the user goal.\n\n"
    "Output JSON -- no markdown, no prose, no code fences, nothing before "
    "or after the JSON. Two shapes are accepted:\n\n"
    "  (A) A SINGLE action object -- when the next step depends on a UI "
    "state you have not yet observed:\n"
    "      {\"action\": \"click\", \"x\": <int>, \"y\": <int>}\n\n"
    "  (B) A LIST of action objects -- a plan-then-execute batch, "
    "executed in order with no fresh screenshot between items. Use this "
    "when the current screenshot already shows EVERY target the batch "
    "will touch (e.g. you can already see the Calc button \"7\" and \"=\" "
    "so you can batch the clicks safely). Max 6 actions per batch. "
    "Insert a wait between actions that need the UI to settle:\n"
    "      [{\"action\": \"click\", \"x\": 100, \"y\": 200},\n"
    "       {\"action\": \"wait\",  \"ms\": 250},\n"
    "       {\"action\": \"click\", \"x\": 100, \"y\": 280}]\n\n"
    "Allowed action shapes:\n"
    "  {\"action\": \"click_element\", \"name\": \"<UI element label>\"}\n"
    "  {\"action\": \"click\",         \"x\": <int>, \"y\": <int>}\n"
    "  {\"action\": \"type\",          \"text\": \"<string>\"}\n"
    "  {\"action\": \"key\",           \"key\": \"<enter|tab|esc|...>\"}\n"
    "  {\"action\": \"scroll\",        \"direction\": \"<up|down|left|right>\", "
    "\"amount\": <int notches, default 3>}\n"
    "  {\"action\": \"open_app\",      \"name\": \"<app name>\"}\n"
    "  {\"action\": \"wait\",          \"ms\": <int 0-10000>}\n"
    "  {\"action\": \"done\"}\n"
    "  {\"action\": \"fail\", \"reason\": \"<short string>\"}\n\n"
    "COORDINATE SYSTEM (read carefully -- this is the #1 source of bugs):\n"
    "* x and y are on a 0-1000 NORMALIZED grid relative to the screenshot, "
    "NOT raw pixels. x = horizontal position: 0 = left edge, 500 = "
    "horizontal center, 1000 = right edge. y = vertical position: 0 = top "
    "edge, 500 = vertical center, 1000 = BOTTOM edge. Origin top-left.\n"
    "* Concrete anchors: the dead center of the screen is x=500, y=500. A "
    "bottom toolbar / media player bar (e.g. Spotify's play/pause controls) "
    "sits near y=950-985. A top menu bar sits near y=15-40. The far-right "
    "edge is x~985.\n"
    "* Aim for the CENTER of the target element. Read its position from the "
    "image as a fraction of the screen and express it on the 0-1000 grid.\n\n"
    "GROUNDING POLICY (read carefully -- this is the #1 source of wrong clicks):\n"
    "* PREFER ``click_element`` (by accessibility NAME) for ANY control that "
    "has a readable label: buttons, menu items, list rows, tabs, checkboxes, "
    "text fields. Native apps -- Calculator, Notepad, Settings, File Explorer, "
    "Office, dialogs -- expose clean accessibility names. When an "
    "'AVAILABLE CONTROLS' list is given below, use one of those EXACT names. "
    "This is DETERMINISTIC: it clicks the control's true center, immune to "
    "resolution, window size, and monitor offset -- pixel-guessing a small "
    "button inside a big screen does NOT work.\n"
    "* Use the control's German label or its name exactly as listed -- e.g. "
    "the Calculator keys are \"Acht\" (8), \"Sieben\" (7), \"Neun\" (9), "
    "\"Multiplizieren mit\" (x), \"Plus\" (+), \"Minus\" (-), "
    "\"Dividieren durch\" (/), \"Gleich\" (=). NEVER pass a bare symbol like "
    "\"8\", \"x\" or \"+\" to click_element -- \"x\" matches the window's "
    "Maximize button, not multiply.\n"
    "* Use pixel ``click`` (x, y) ONLY when the target has NO usable label -- "
    "media scrubbers / transport bars (Spotify play/pause), game canvases, "
    "video surfaces, custom-painted UIs -- OR when click_element reports the "
    "label is genuinely absent from the available controls.\n"
    "* To type into a field: first focus it (click_element the field, or "
    "click it), then ``type``. Never type blindly into an unfocused screen.\n"
    "* If click_element misses, it returns the available labels -- pick the "
    "closest real one; do NOT fall back to blind pixel-guessing on a small "
    "control.\n"
    "* If the target (a chat, a list row, a button, page content) is NOT "
    "visible in the current screenshot, use ``scroll`` (direction up/down/"
    "left/right) to bring it into view, then re-observe -- do NOT guess a "
    "click on something you cannot see.\n\n"
    "BATCHING (LATENCY matters):\n"
    "* You MAY return a LIST of up to 6 actions when the current screenshot "
    "already shows EVERY target the batch will touch. One LLM call with a "
    "batch is much faster than one call per action. Insert "
    "{\"action\": \"wait\", \"ms\": N} between actions that need the UI to "
    "settle.\n"
    "* Do NOT batch past an unrevealed UI: after ``open_app`` or any action "
    "that changes the screen, STOP the batch and let the next screenshot "
    "show the result. When unsure, return a single action.\n\n"
    "APP LAUNCH:\n"
    "* Use ``open_app`` for launch goals (\"open Spotify\", \"oeffne den "
    "Rechner\"). Common names: \"spotify\", \"calc\", \"notepad\", "
    "\"chrome\", \"edge\", \"explorer\", \"cmd\".\n"
    "* If the target app is ALREADY VISIBLE in the current screenshot, do "
    "NOT call open_app again -- re-launching steals window focus and resets "
    "your progress. Interact with the app's existing window instead.\n"
    "* DO NOT type the app name into a focused field hoping it is a search "
    "box -- use open_app to launch.\n\n"
    "Hard rules:\n"
    "* x and y are 0-1000 normalized (see COORDINATE SYSTEM above), top-left "
    "origin. NEVER return raw pixel values -- a 4K screen is still 0-1000.\n"
    "* NEVER wrap the JSON in ```json``` fences or any other markup.\n\n"
    "GOAL COMPLETION DISCIPLINE (Scrooge-anti-pattern -- you do NOT get to\n"
    "declare victory by doing nothing):\n"
    "* The user's goal is in the user message ('GOAL: ...'). Before you\n"
    "  respond, internally restate the goal and the OBSERVABLE PROOF that\n"
    "  would mean it is achieved (e.g. 'song is playing' -> a pause-button\n"
    "  icon visible AND a current-time progress > 0:00).\n"
    "* Use \"done\" ONLY when that observable proof is in the CURRENT\n"
    "  screenshot. If the proof is missing, even if the previous step\n"
    "  looked like progress, the goal is NOT achieved -- pick the next\n"
    "  concrete action that advances toward the proof.\n"
    "* Use \"fail\" ONLY when you have attempted at least one concrete\n"
    "  action that should have moved toward the goal and the screen still\n"
    "  does not show any element you can usefully click or type into.\n"
    "  Returning \"fail\" without trying anything is FORBIDDEN.\n"
    "* If the screen looks unchanged from your previous step, your last\n"
    "  click landed on empty space or the element was not reactive --\n"
    "  pick a DIFFERENT pixel target this time, do not repeat the same\n"
    "  coordinates.\n"
    "* Default mindset: you ARE making progress until the observable\n"
    "  proof is visible. Inaction is not a valid outcome -- pick the\n"
    "  most plausible click target and try it.\n\n"
    "MEDIA TRANSPORT BUTTONS (play/pause are a TOGGLE -- read them like a\n"
    "human). A transport button always shows the icon for what your NEXT\n"
    "click would DO, not the current state:\n"
    "* A PAUSE glyph (two vertical bars) visible => media is ALREADY\n"
    "  PLAYING. If the goal was 'play X', THE GOAL IS DONE -- emit\n"
    "  {\"action\": \"done\"}. Do NOT click it: clicking a pause glyph STOPS\n"
    "  playback (toggles it off).\n"
    "* A PLAY glyph (right-pointing triangle) visible => media is STOPPED.\n"
    "  Click it ONCE to start, then STOP and let the next screenshot confirm.\n"
    "* After ONE click on a transport/play/pause/submit/send control, NEVER\n"
    "  click it again in the same mission unless a later screenshot clearly\n"
    "  proves it is in the WRONG state. A second click just toggles your\n"
    "  success away. If you already clicked it and it now shows the success\n"
    "  state, emit done.\n"
    "* You can see your own PREVIOUS_STEPS history -- if you already clicked a\n"
    "  control, do not click it again; verify the result instead.\n"
)


# ---------------------------------------------------------------------------
# JSON action parsing
# ---------------------------------------------------------------------------

def _parse_action(raw: str) -> dict[str, Any]:
    """Parse a single-action JSON response and validate the schema.

    Accepts a raw JSON object, optionally wrapped in markdown fences (for
    robustness against models that ignore the no-fence rule). Validates
    presence and type of every required field per action.

    Raises ``CULoopError`` on any malformed input.
    """
    if not raw or not raw.strip():
        raise CULoopError("empty model response")
    cleaned = raw.strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    # Try the whole cleaned payload first -- strict mode. If the model
    # returned a JSON array (a common malformation), the isinstance check
    # below rejects it cleanly instead of accidentally extracting the
    # first object via the substring fallback.
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: extract the brace region for prose-wrapped responses.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise CULoopError(f"no JSON object in response: {raw[:120]!r}")
        blob = cleaned[start : end + 1]
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise CULoopError(f"invalid JSON ({exc}): {blob[:120]!r}") from exc
    if not isinstance(obj, dict):
        raise CULoopError(f"JSON root is not an object: {type(obj).__name__}")
    action = obj.get("action")
    if action not in _VALID_ACTIONS:
        raise CULoopError(
            f"unknown action {action!r}; allowed: {sorted(_VALID_ACTIONS)}"
        )
    if action == "click":
        x, y = obj.get("x"), obj.get("y")
        # bool is a subclass of int in Python -- exclude it explicitly.
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise CULoopError("click action requires integer x and y")
        if isinstance(y, bool) or not isinstance(y, (int, float)):
            raise CULoopError("click action requires integer x and y")
        obj["x"], obj["y"] = int(x), int(y)
    elif action == "type":
        text = obj.get("text")
        if not isinstance(text, str):
            raise CULoopError("type action requires a string text field")
        obj["text"] = text
    elif action == "key":
        # Keyboard key / combo press (e.g. Enter to submit a search, Ctrl+A).
        # Accept {"key": "enter"} (single) or {"keys": ["ctrl","a"]} (combo);
        # normalise to a non-empty ``keys`` list dispatched via the hotkey tool.
        keys = obj.get("keys")
        if keys is None and isinstance(obj.get("key"), str):
            keys = [obj["key"]]
        if not isinstance(keys, list) or not keys or not all(
            isinstance(k, str) and k.strip() for k in keys
        ):
            raise CULoopError(
                "key action requires a non-empty 'key' string or 'keys' list "
                "(e.g. {\"action\":\"key\",\"key\":\"enter\"})"
            )
        obj["keys"] = [k.strip().lower() for k in keys]
    elif action == "scroll":
        # Mouse-wheel scroll (Wave 2) — reveal off-screen list rows / page
        # content. direction required; amount defaults so "scroll down" works.
        direction = obj.get("direction")
        if not isinstance(direction, str) or direction.strip().lower() not in _SCROLL_DIRECTIONS:
            raise CULoopError(
                "scroll action requires a 'direction' of up/down/left/right "
                "(e.g. {\"action\":\"scroll\",\"direction\":\"down\"})"
            )
        obj["direction"] = direction.strip().lower()
        amount = obj.get("amount", _DEFAULT_SCROLL_AMOUNT)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise CULoopError("scroll action 'amount' must be a number of wheel notches")
        obj["amount"] = max(1, int(amount))
    elif action == "open_app":
        # Restored 2026-05-27 after observing the loop fall back to ``type
        # "calc"`` into the focused chat input when asked to "open Calc" —
        # the model had no other primitive that semantically meant
        # "launch an app", so it picked the worst fit.
        name = obj.get("name")
        if not isinstance(name, str) or not name.strip():
            raise CULoopError("open_app action requires a non-empty string name field")
        obj["name"] = name.strip()
    elif action == "click_element":
        # UIA-grounded click — the user says e.g. ``click_element name="7"``
        # and ClickElementTool resolves the exact pixel coords from the
        # UIAutomation tree. Added 2026-05-27 after the live test ``oeffne
        # Rechner und klick auf 7`` saw Gemini Flash guess (646, 984) /
        # (36, 262) and miss the Calc 7-button entirely — Vision LLMs
        # cannot reliably ground raw pixel coords from a screenshot, and
        # UIA names are the deterministic fix.
        name = obj.get("name")
        if not isinstance(name, str) or not name.strip():
            raise CULoopError(
                "click_element action requires a non-empty string name field"
            )
        obj["name"] = name.strip()
    elif action == "wait":
        # Lets the model batch-plan ``[click, wait, click]`` so a UI has time
        # to settle between two clicks without spending an LLM round-trip on
        # the second screenshot. Capped at _MAX_WAIT_MS to defend against a
        # hallucinated "wait 1 hour".
        ms = obj.get("ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)):
            raise CULoopError("wait action requires a numeric ms field")
        if ms < 0:
            raise CULoopError("wait action ms must be >= 0")
        obj["ms"] = min(int(ms), _MAX_WAIT_MS)
    elif action == "fail":
        if not isinstance(obj.get("reason"), str):
            raise CULoopError("fail action requires a string reason field")
    return obj


def _parse_actions(raw: str) -> list[dict[str, Any]]:
    """Parse and validate a batch of actions from a model response.

    Accepts either:

      * a single action object — wrapped into a one-element list so the
        executor can iterate uniformly (backward compatibility with
        models that ignore the batch invitation in the system prompt), or
      * a JSON list of action objects — used for plan-then-execute when
        the model can see every target in the current screenshot.

    Trims oversized batches to :data:`_MAX_BATCH` items as a defence
    against runaway plans. Re-uses :func:`_parse_action` to validate
    each item, so all the per-action rules apply identically.
    """
    if not raw or not raw.strip():
        raise CULoopError("empty model response")
    cleaned = raw.strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    try:
        root = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Same prose-wrapped recovery as _parse_action for symmetry.
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            raise CULoopError(
                f"invalid JSON ({exc}): {cleaned[:120]!r}",
            ) from exc
        try:
            root = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc2:
            raise CULoopError(
                f"invalid JSON ({exc2}): {cleaned[start:end+1][:120]!r}",
            ) from exc2

    if isinstance(root, dict):
        # Re-validate the single action via _parse_action so the same rules
        # apply; pass the already-decoded dict back as a JSON string. The
        # quick path: just hand it to _parse_action by re-serialising — but
        # that's wasteful. Inline the per-action validation instead.
        return [_validate_action_dict(root)]

    if not isinstance(root, list):
        raise CULoopError(
            f"JSON root must be an object or a list of objects, got "
            f"{type(root).__name__}"
        )
    if not root:
        raise CULoopError("empty actions list — model returned []")

    if len(root) > _MAX_BATCH:
        root = root[:_MAX_BATCH]  # truncate runaway plans

    return [_validate_action_dict(item) for item in root]


def _validate_action_dict(obj: Any) -> dict[str, Any]:
    """Validate one already-decoded action dict and return it normalised."""
    if not isinstance(obj, dict):
        raise CULoopError(
            f"action item must be an object, got {type(obj).__name__}"
        )
    action = obj.get("action")
    if action not in _VALID_ACTIONS:
        raise CULoopError(
            f"unknown action {action!r}; allowed: {sorted(_VALID_ACTIONS)}"
        )
    if action == "click":
        x, y = obj.get("x"), obj.get("y")
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise CULoopError("click action requires integer x and y")
        if isinstance(y, bool) or not isinstance(y, (int, float)):
            raise CULoopError("click action requires integer x and y")
        obj["x"], obj["y"] = int(x), int(y)
    elif action == "type":
        text = obj.get("text")
        if not isinstance(text, str):
            raise CULoopError("type action requires a string text field")
        obj["text"] = text
    elif action == "key":
        # Keyboard key / combo press (e.g. Enter to submit a search, Ctrl+A).
        # Accept {"key": "enter"} (single) or {"keys": ["ctrl","a"]} (combo);
        # normalise to a non-empty ``keys`` list dispatched via the hotkey tool.
        keys = obj.get("keys")
        if keys is None and isinstance(obj.get("key"), str):
            keys = [obj["key"]]
        if not isinstance(keys, list) or not keys or not all(
            isinstance(k, str) and k.strip() for k in keys
        ):
            raise CULoopError(
                "key action requires a non-empty 'key' string or 'keys' list "
                "(e.g. {\"action\":\"key\",\"key\":\"enter\"})"
            )
        obj["keys"] = [k.strip().lower() for k in keys]
    elif action == "scroll":
        # Mouse-wheel scroll for lists/pages (chats, file pickers, web pages).
        # direction is required; amount defaults so the model can just say
        # "scroll down". Optional x/y target the wheel at a region (same
        # 0-1000 normalized grid as click — resolved at execute time).
        direction = obj.get("direction")
        if not isinstance(direction, str) or direction.strip().lower() not in _SCROLL_DIRECTIONS:
            raise CULoopError(
                "scroll action requires a 'direction' of up/down/left/right "
                "(e.g. {\"action\":\"scroll\",\"direction\":\"down\"})"
            )
        obj["direction"] = direction.strip().lower()
        amount = obj.get("amount", _DEFAULT_SCROLL_AMOUNT)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise CULoopError("scroll action 'amount' must be a number of wheel notches")
        obj["amount"] = max(1, int(amount))
    elif action == "open_app":
        name = obj.get("name")
        if not isinstance(name, str) or not name.strip():
            raise CULoopError("open_app action requires a non-empty string name field")
        obj["name"] = name.strip()
    elif action == "wait":
        ms = obj.get("ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)):
            raise CULoopError("wait action requires a numeric ms field")
        if ms < 0:
            raise CULoopError("wait action ms must be >= 0")
        obj["ms"] = min(int(ms), _MAX_WAIT_MS)
    elif action == "fail":
        if not isinstance(obj.get("reason"), str):
            raise CULoopError("fail action requires a string reason field")
    return obj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Per-OPERATION timeout cap (BUG-CU-STALL, 2026-05-29). The whole-mission
# budget (harness_timeout_s) was equal to the per-op timeout (both 30s), so a
# SINGLE slow operation (a blocking Windows-UIA COM enumeration, a cold brain
# call) ate the entire mission budget and the loop did NOTHING for 28s, then
# timed out. Capping every op well below the mission budget means one slow op
# costs at most this, and the loop keeps moving. The UIA enumeration gets an
# even tighter budget because a wedged COM call cannot be cancelled (the
# asyncio.wait_for over a to_thread call only stops awaiting; the thread runs
# on) -- so we must never give it a large budget.
_PER_OP_TIMEOUT_CAP_S = 12.0
_UIA_TIMEOUT_S = 3.0
_OBSERVE_TIMEOUT_S = 6.0


def _timeout_s(ctx: "ComputerUseContext") -> float:
    """Per-operation timeout: the configured per_step value, but hard-capped so
    no single op can consume the whole mission budget (BUG-CU-STALL)."""
    cfg_v = float(getattr(ctx, "per_step_timeout_s", 30.0) or 30.0)
    return max(0.001, min(cfg_v, _PER_OP_TIMEOUT_CAP_S))


def _is_cancelled(cancel_token: CancelToken | None) -> bool:
    return bool(cancel_token is not None and cancel_token.is_cancelled())


def _capture_monitor_geometry() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the foreground monitor in
    virtual-desktop coordinates.

    BUG-CU-MULTIMON (live 2026-05-28): the screenshot loop captures the
    FOREGROUND monitor (jarvis/vision/screenshot.py::select_capture_monitor).
    The captured image is 0-based, so the vision model returns image-relative
    coordinates. Two facts about those coordinates must both be handled:

    1. SCALE — Gemini returns spatial coordinates on a 0-1000 NORMALIZED grid
       (documented Gemini behaviour, confirmed by the 5-agent deep-dive
       2026-05-28), NOT raw pixels. The loop must convert
       ``pixel = norm / 1000 * monitor_dimension`` before clicking. We scale
       against the MONITOR dimensions (not the image dimensions) so the
       conversion stays correct even if the screenshot is downscaled before
       being sent to the model.
    2. ORIGIN — on a multi-monitor virtual desktop a non-primary monitor can
       have a NEGATIVE origin (e.g. a left monitor at left=-3840).
       ``SetCursorPos`` (jarvis/control/cursor_motion.py) takes ABSOLUTE
       virtual-desktop coordinates, so the monitor origin must be ADDED after
       the normalize-to-pixel step.

    Sourcing left/top AND width/height from the SAME GetMonitorInfo call keeps
    origin and dimensions atomically consistent (no second GetForegroundWindow
    that could read a different monitor).

    Returns (0, 0, 0, 0) on any failure or on non-Windows hosts. Callers must
    treat width/height == 0 as "unknown" and fall back to passing the model
    coordinates through unscaled (correct for headless/Linux: no GUI to click,
    and no hard win32 dependency -- cloud-first doctrine).
    """
    try:
        import win32api  # noqa: PLC0415
        import win32con  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return (0, 0, 0, 0)
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return (0, 0, 0, 0)
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(mon)
        left, top, right, bottom = info["Monitor"]
        return (int(left), int(top), int(right - left), int(bottom - top))
    except Exception:  # noqa: BLE001
        return (0, 0, 0, 0)


# Gemini returns spatial coordinates on this normalized grid (0..NORM_MAX).
_COORD_NORM_MAX = 1000


async def _observe(
    ctx: "ComputerUseContext",
    cancel_token: CancelToken | None,
) -> Observation:
    """Capture one screenshot and emit ObservationCaptured."""
    obs = await asyncio.wait_for(
        ctx.vision_engine.observe(mode="auto", cancel_token=cancel_token),
        timeout=_timeout_s(ctx),
    )
    if ctx.bus is not None:
        try:
            await ctx.bus.publish(ObservationCaptured(
                trace_id=obs.trace_id,
                timestamp_ns=obs.timestamp_ns,
                source=obs.source,
                window_title=obs.window_title,
                node_count=0,  # screenshot-only: no UIA enumeration
                screenshot_hash=obs.screenshot_hash,
                screenshot_path=obs.screenshot_path,
            ))
        except Exception:  # noqa: BLE001
            log.debug("ObservationCaptured publish failed", exc_info=True)
    return obs


def _select_fast_model(manager: Any, provider: Any) -> str | None:
    """Pick a fast-tier model for the active provider.

    Order of preference: ``_fast_model`` -> ``_flash_model`` -> ``_step_model``
    -> ``_deep_model``. The first callable that returns a non-empty string
    wins. ``_deep_model`` is the fallback because some test stubs only
    expose the deep selector; production BrainManager always has
    ``_fast_model``.
    """
    for attr in ("_fast_model", "_flash_model", "_step_model", "_deep_model"):
        picker = getattr(manager, attr, None)
        if callable(picker):
            try:
                model = picker(provider)
            except Exception:  # noqa: BLE001
                continue
            if model:
                return str(model)
    return None


# ---------------------------------------------------------------------------
# Brain dispatch
# ---------------------------------------------------------------------------

async def _call_brain(
    ctx: "ComputerUseContext",
    *,
    observation: Observation,
    user_goal: str,
    history_text: str,
    system_prompt: str | None = None,
    user_message: str | None = None,
    frame_b: Observation | None = None,
    max_tokens: int = 256,
) -> str:
    """Send screenshot + goal + history to the active brain, return raw text.

    Test-friendly: if ``ctx.brain_manager`` exposes ``complete_text``, that
    single-call shim is used (FakeBrain pattern). Otherwise route through
    the BrainManager's active-provider fast-tier brain with the screenshot
    attached as an ``ImageBlock``.

    ``system_prompt`` / ``user_message`` override the defaults -- the
    on-demand done-verifier reuses this same dispatch (screenshot attach,
    provider selection, fake shim) with a strict-judge prompt.

    Coordinates returned by the model are in the coordinate system of the
    screenshot it was given. The current VisionEngine sends full-resolution
    frames, so no rescaling is applied here; if a future VisionEngine
    downsamples, the caller must rescale coordinates before clicking.
    """
    system_prompt = system_prompt or _SYSTEM_PROMPT
    if user_message is None:
        user_message = (
            f"GOAL: {user_goal}\n"
            f"PREVIOUS_STEPS:\n{history_text or '(none)'}\n\n"
            "Inspect the screenshot and emit ONE JSON action."
        )

    # FakeBrain test shim.
    complete_text = getattr(ctx.brain_manager, "complete_text", None)
    if complete_text is not None:
        result = await complete_text(system=system_prompt, user=user_message)
        return str(result)

    # Production: direct provider dispatch.
    manager = ctx.brain_manager
    if all(hasattr(manager, n) for n in ("_get_brain", "active_provider")):
        from jarvis.brain.streaming import aggregate  # noqa: PLC0415

        provider = manager.active_provider
        if provider is None:
            raise CULoopError("BrainManager.active_provider is None")
        model = _select_fast_model(manager, provider)
        brain = manager._get_brain(provider, model)
        if brain is None:
            raise CULoopError(
                f"BrainManager._get_brain({provider!r}, {model!r}) returned None"
            )

        # Attach screenshot(s). ``frame_b`` lets the two-frame motion verifier
        # send Frame A + Frame B in one call so the model can compare them.
        from jarvis.brain.router import _read_observation_image_b64  # noqa: PLC0415

        images: list[ImageBlock] = []
        for obs in (observation, frame_b):
            if obs is None or not obs.screenshot_path:
                continue
            try:
                mime, image_b64 = await _read_observation_image_b64(obs)
                images.append(ImageBlock(
                    mime=mime,
                    data_b64=image_b64,
                    source_hash=obs.screenshot_hash,
                ))
                log.info(
                    "ComputerUseLoop screenshot attached: hash=%s len=%d",
                    obs.screenshot_hash[:16] if obs.screenshot_hash else "?",
                    len(image_b64),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("ComputerUseLoop screenshot attach failed: %s", exc)

        req = BrainRequest(
            messages=(BrainMessage(
                role="user", content=user_message, images=tuple(images),
            ),),
            system=system_prompt,
            temperature=0.0,
            max_tokens=max_tokens,
            stream=True,
        )
        agg = await aggregate(brain.complete(req))
        return agg.text

    # Last-resort callable manager (legacy stub).
    if callable(manager):
        return str(await manager(f"{system_prompt}\n\n{user_message}"))


async def _decide_native_batch(
    ctx: "ComputerUseContext",
    observation: Observation,
    task_prompt: str,
    history: list[str],
    step_idx: int,
) -> list[dict[str, Any]] | None:
    """Wave 3 hybrid: ask the native Gemini computer_use engine for the next
    action(s). Returns validated loop-action dicts, or ``None`` when native is
    disabled/unavailable or fails for ANY reason -- the caller then runs the
    hand-rolled vision+JSON path for this step. Default: ``ctx.native_cu`` is
    None (``[computer_use].prefer_native`` defaults False), so this is a no-op.
    """
    native = getattr(ctx, "native_cu", None)
    if native is None:
        return None
    # Reuse the existing observation->image reader (handles path + mime), then
    # decode to raw bytes for the native call.
    try:
        from jarvis.brain.router import _read_observation_image_b64  # noqa: PLC0415

        _mime, image_b64 = await _read_observation_image_b64(observation)
        screenshot = base64.b64decode(image_b64)
    except Exception as exc:  # noqa: BLE001
        log.info("[cu] native CU screenshot read failed (step %d): %s", step_idx, exc)
        return None
    try:
        actions = await asyncio.wait_for(
            native.decide(
                screenshot_png=screenshot,
                goal=task_prompt,
                history=list(history[-12:]),
            ),
            timeout=_timeout_s(ctx),
        )
    except Exception as exc:  # noqa: BLE001 — any native failure -> hand-rolled fallback
        log.info("[cu] native CU decide failed (step %d), falling back: %s", step_idx, exc)
        return None
    if not actions:
        return None
    # Defense-in-depth: validate through the same schema the hand-rolled path
    # uses, so a mapping bug can never feed a malformed action to the executor.
    try:
        validated = [_validate_action_dict(dict(a)) for a in actions]
    except CULoopError as exc:
        log.info(
            "[cu] native CU produced invalid action (step %d), falling back: %s",
            step_idx, exc,
        )
        return None
    log.info(
        "[cu] step %d used native Gemini computer_use (%d action(s))",
        step_idx, len(validated),
    )
    return validated

    raise CULoopError(
        "BrainManager exposes neither complete_text, _get_brain, nor __call__"
        " -- screenshot-only loop cannot dispatch."
    )


async def _decide_native_batch(
    ctx: "ComputerUseContext",
    observation: Observation,
    task_prompt: str,
    history: list[str],
    step_idx: int,
) -> list[dict[str, Any]] | None:
    """Wave 3 hybrid: ask the native Gemini computer_use engine for the next
    action(s). Returns validated loop-action dicts, or ``None`` when native is
    disabled/unavailable or fails for ANY reason -- the caller then runs the
    hand-rolled vision+JSON path for this step. Default: ``ctx.native_cu`` is
    None (``[computer_use].prefer_native`` defaults False), so this is a no-op.
    """
    native = getattr(ctx, "native_cu", None)
    if native is None:
        return None
    # Reuse the existing observation->image reader (handles path + mime), then
    # decode to raw bytes for the native call.
    try:
        from jarvis.brain.router import _read_observation_image_b64  # noqa: PLC0415

        _mime, image_b64 = await _read_observation_image_b64(observation)
        screenshot = base64.b64decode(image_b64)
    except Exception as exc:  # noqa: BLE001
        log.info("[cu] native CU screenshot read failed (step %d): %s", step_idx, exc)
        return None
    try:
        actions = await asyncio.wait_for(
            native.decide(
                screenshot_png=screenshot,
                goal=task_prompt,
                history=list(history[-12:]),
            ),
            timeout=_timeout_s(ctx),
        )
    except Exception as exc:  # noqa: BLE001 — any native failure -> hand-rolled fallback
        log.info("[cu] native CU decide failed (step %d), falling back: %s", step_idx, exc)
        return None
    if not actions:
        return None
    # Defense-in-depth: validate through the same schema the hand-rolled path
    # uses, so a mapping bug can never feed a malformed action to the executor.
    try:
        validated = [_validate_action_dict(dict(a)) for a in actions]
    except CULoopError as exc:
        log.info(
            "[cu] native CU produced invalid action (step %d), falling back: %s",
            step_idx, exc,
        )
        return None
    log.info(
        "[cu] step %d used native Gemini computer_use (%d action(s))",
        step_idx, len(validated),
    )
    return validated


# ---------------------------------------------------------------------------
# On-demand done-verifier (BUG-CU-TOGGLE, 2026-05-28)
# ---------------------------------------------------------------------------

# Goals whose success is a reversible STATE CHANGE (a toggle/submit) where the
# planner is prone to re-clicking and undoing its own work. Only these arm the
# on-demand verifier -- a navigation/open goal does not need it.
_VERIFY_GOAL_RE = re.compile(
    r"\b(spiel|abspiel|play|pausier|pause|stopp|stop|"
    r"submit|absenden|senden|send|enter|start|abschick)",
    re.I,
)

# Compute / deterministic-result goals (calculator etc.) where "done" must be
# checked against the actual RESULT on screen, not just "an action happened"
# (BUG-CU-RESULT, 2026-05-29: Calc showed 130 but the loop reported done).
_GOAL_NEEDS_RESULT_RE = re.compile(
    r"rechne|berechne|calculate|wie\s*viel|ergebnis|\d\s*(?:mal|plus|minus|"
    r"geteilt|durch|[+\-x*/])\s*\d",
    re.I,
)


def _goal_needs_result(goal: str) -> bool:
    return bool(_GOAL_NEEDS_RESULT_RE.search(goal or ""))

# Play goals that require SEARCHING for and selecting a NEW track (so the loop
# cannot satisfy them by resuming the already-loaded song). Pause/stop are
# excluded -- they act on the current track and need no search.
_GOAL_NEEDS_SEARCH_RE = re.compile(r"\b(spiel|abspiel|play)\b", re.I)


def _goal_needs_search(goal: str) -> bool:
    return bool(_GOAL_NEEDS_SEARCH_RE.search(goal or ""))

# Interval between the two verification frames -- long enough for a real
# media timer to tick at least 1 second, short enough to barely add latency.
_VERIFY_FRAME_GAP_S = 1.3

_VERIFIER_SYSTEM_PROMPT = (
    "You are a STRICT completion judge for a desktop automation task. You are "
    "given TWO screenshots: Frame A first, then Frame B captured ~1.3 seconds "
    "later. Output exactly ONE JSON object, no prose, no code fences: "
    "{\"done\": true|false, \"proof\": \"<exact element + the two values you "
    "compared>\"}\n"
    "Rules:\n"
    "* \"done\": true ONLY if the screenshots PROVE the goal is achieved RIGHT "
    "NOW. Never guess. When unsure, answer false.\n"
    "* For a 'play <media>' goal, proof requires MOTION between the frames: "
    "the elapsed-time counter in Frame B must be STRICTLY GREATER than in "
    "Frame A (e.g. 0:03 -> 0:05), OR the progress bar visibly advanced. A "
    "PAUSE glyph alone is NOT enough -- a paused/loaded track also shows it. "
    "If the timer is identical, frozen, blank, 0:00, or you cannot read it in "
    "BOTH frames, answer done:false. Quote BOTH timer values in 'proof'.\n"
    "* For a 'submit/send' goal: the sent message/row must visibly appear in "
    "the conversation/result area (not just typed in the input box).\n"
    "* If the required proof is absent or ambiguous, done:false.\n"
)


def _goal_needs_verification(goal: str) -> bool:
    # Arm the verifier for state-change goals (play/submit) AND compute goals
    # (calculator) -- the latter so a wrong result cannot be reported as done.
    return bool(_VERIFY_GOAL_RE.search(goal or "")) or _goal_needs_result(goal)


_COMPUTE_VERIFIER_SYSTEM_PROMPT = (
    "You are a STRICT result judge for a calculator task. Read the calculator's "
    "result display in the screenshot. Output exactly ONE JSON object, no prose, "
    "no fences: {\"done\": true|false, \"proof\": \"display shows <X>, expected "
    "<Y>\"}\n"
    "Compute the correct answer to the GOAL's arithmetic yourself (German: 'mal'="
    "x, 'plus'=+, 'minus'=-, 'geteilt durch'=/). done:true ONLY if the value on "
    "the calculator's main result line EQUALS your computed answer. If the "
    "display shows a different number, an expression still being entered, or you "
    "cannot read it, done:false. Always quote the displayed value and your "
    "expected value in proof."
)


async def _verify_goal_done(
    ctx: "ComputerUseContext",
    *,
    observation: Observation,
    user_goal: str,
) -> tuple[bool, str]:
    """Two-frame MOTION verifier (BUG-CU-FALSE-DONE, 2026-05-28).

    A single screenshot cannot prove media is *playing* -- a pause glyph
    appears on a loaded-but-paused track too, which is how the loop falsely
    reported done. So: Frame A = the passed observation; wait ~1.3s; Frame B =
    a fresh capture. If the two frames are byte-identical (frozen screen) the
    timer is not advancing -> NOT done, with ZERO LLM cost. Only if they
    differ do we ask the judge (with BOTH frames) whether the elapsed timer
    advanced.

    Returns ``(done, proof)``. Never raises -- on any error returns
    ``(False, "")`` so verification can only HELP, never block the loop."""
    # Compute goals (calculator) are a SINGLE-frame check: the model reads the
    # result display and compares it to the arithmetic it computes itself. No
    # motion / two-frame gap needed (BUG-CU-RESULT).
    if _goal_needs_result(user_goal):
        try:
            raw = await asyncio.wait_for(
                _call_brain(
                    ctx,
                    observation=observation,
                    user_goal=user_goal,
                    history_text="",
                    system_prompt=_COMPUTE_VERIFIER_SYSTEM_PROMPT,
                    user_message=(
                        f"GOAL: {user_goal}\n\n"
                        "Read the calculator result and judge. JSON object only."
                    ),
                ),
                timeout=_timeout_s(ctx),
            )
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.debug("[cu] compute-verifier failed (non-fatal): %s", exc)
            return (False, "")
        return _parse_verdict(raw)

    # Frame B: a fresh capture after a short gap so a real timer can tick.
    try:
        await asyncio.sleep(_VERIFY_FRAME_GAP_S)
        frame_b = await asyncio.wait_for(
            _observe(ctx, None), timeout=_timeout_s(ctx),
        )
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] verifier frame-B capture failed (non-fatal): %s", exc)
        return (False, "")

    # Zero-LLM pre-gate: identical hashes => nothing on screen moved => the
    # timer is not advancing => definitely not playing.
    if (observation.screenshot_hash
            and observation.screenshot_hash == frame_b.screenshot_hash):
        return (False, "screen frozen between frames — timer not advancing")

    user_message = (
        f"GOAL: {user_goal}\n\n"
        "Frame A is the first screenshot, Frame B was captured ~1.3s later. "
        "Judge per the rules. Reply with the JSON object only."
    )
    try:
        raw = await asyncio.wait_for(
            _call_brain(
                ctx,
                observation=observation,
                user_goal=user_goal,
                history_text="",
                system_prompt=_VERIFIER_SYSTEM_PROMPT,
                user_message=user_message,
                frame_b=frame_b,
            ),
            timeout=_timeout_s(ctx),
        )
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] verifier call failed/timed out (non-fatal): %s", exc)
        return (False, "")
    return _parse_verdict(raw)


def _parse_verdict(raw: str) -> tuple[bool, str]:
    """Parse a strict-judge JSON {"done":bool,"proof":str} (fence-tolerant).
    Returns (False, "") on any malformed input -- verification never blocks."""
    import json as _json  # noqa: PLC0415
    cleaned = (raw or "").strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return (False, "")
    try:
        verdict = _json.loads(cleaned[start : end + 1])
    except Exception:  # noqa: BLE001
        return (False, "")
    done = bool(verdict.get("done") is True)
    proof = str(verdict.get("proof", ""))[:160]
    return (done, proof)


# ---------------------------------------------------------------------------
# Plan-first planner (BUG-CU-NO-PLAN, 2026-05-28)
# ---------------------------------------------------------------------------
#
# The reactive loop re-decides the next single action from scratch each step,
# so for a multi-step task ("play a song" = open -> search -> type -> select ->
# play) it loses the thread, mashes the most obvious button, and the verifier
# rubber-stamps it. Like Claude-in-Chrome, we make an ordered PLAN first and
# feed it into every executor turn as context, so the model knows the whole
# task structure and which step it is on. The plan is GUIDANCE (the model still
# grounds each click against the live screenshot); it is not a rigid macro.

_PLANNER_SYSTEM_PROMPT = (
    "You are a desktop-automation planner. Look at the screenshot, then output "
    "an ordered plan to accomplish the user goal as a JSON object -- no prose, "
    "no code fences: {\"plan\": [{\"intent\": \"<one atomic UI action, "
    "imperative>\", \"success\": \"<the concrete thing VISIBLE on screen once "
    "this step is done>\"}, ...]}\n"
    "Rules:\n"
    "* 3-7 steps. Each step is ONE atomic action: open / click / type / select "
    "/ press.\n"
    "* Decompose properly. 'play a song' MUST become: open the app -> click the "
    "search box -> type the song name -> press Enter -> click the matching "
    "track row -> press play. NEVER collapse it to a single 'click play'.\n"
    "* 'success' must be something a person could SEE on screen (text in a box, "
    "a result row, a now-playing title, an advancing timer) -- never an "
    "assumption.\n"
    "* The final step's success for media playback is: the elapsed-time counter "
    "is ADVANCING (the song is audibly playing), not merely a pause glyph.\n"
)


async def _make_plan(
    ctx: "ComputerUseContext",
    *,
    observation: Observation,
    user_goal: str,
) -> list[dict[str, str]]:
    """Generate an ordered step plan for the goal. Returns ``[]`` on any
    failure -- the loop then runs its stateless reactive path unchanged
    (graceful degrade, no regression)."""
    user_message = (
        f"GOAL: {user_goal}\n\n"
        "Produce the ordered plan as the JSON object only."
    )
    try:
        raw = await asyncio.wait_for(
            _call_brain(
                ctx,
                observation=observation,
                user_goal=user_goal,
                history_text="",
                system_prompt=_PLANNER_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=512,
            ),
            timeout=_timeout_s(ctx),
        )
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] planner call failed/timed out (non-fatal): %s", exc)
        return []
    import json as _json  # noqa: PLC0415
    cleaned = (raw or "").strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        obj = _json.loads(cleaned[start : end + 1])
    except Exception:  # noqa: BLE001
        return []
    raw_steps = obj.get("plan") if isinstance(obj, dict) else None
    if not isinstance(raw_steps, list) or not raw_steps:
        return []
    steps: list[dict[str, str]] = []
    for s in raw_steps[:8]:
        if isinstance(s, dict) and s.get("intent"):
            steps.append({
                "intent": str(s.get("intent", "")).strip(),
                "success": str(s.get("success", "")).strip(),
            })
    return steps


def _render_plan(plan: list[dict[str, str]], current: int) -> str:
    """Render the plan as a checklist for the executor prompt, marking the
    current step. ``current`` is 0-based."""
    lines = []
    for i, step in enumerate(plan):
        marker = ">>>" if i == current else ("[x]" if i < current else "[ ]")
        lines.append(f" {i + 1}. {marker} {step['intent']}")
    return "\n".join(lines)


# UIA control roles a click_element can usefully target. Used to build the
# per-step "AVAILABLE CONTROLS" list so the model picks a real name instead of
# pixel-guessing (BUG-CU-GROUNDING, 2026-05-29).
_CLICKABLE_UIA_ROLES = frozenset({
    "Button", "MenuItem", "ListItem", "TabItem", "CheckBox", "RadioButton",
    "Hyperlink", "Edit", "ComboBox", "TreeItem", "SplitButton", "Text",
})


async def _foreground_clickable_labels(timeout_s: float, max_n: int = 28) -> list[str]:
    """Enumerate the foreground window's clickable UIA control names so the
    executor can click_element by an EXACT real name. Returns ``[]`` on any
    failure OR when the foreground exposes no usable labels (media players,
    games, canvases) -- that empty path self-gates the loop back to pixel
    clicks, so no app allowlist is needed. Never raises.

    Prefers the AutomationId when it is more stable than the localized Name
    (e.g. Calculator: name="Acht" / automation_id="num8Button"); we surface the
    Name (what the model reads on screen) and let click_element resolve it.
    """
    try:
        from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

        obs = await asyncio.wait_for(make_ui_tree_source().observe(), timeout=timeout_s)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] UI-tree label enumeration failed (non-fatal): %s", exc)
        return []
    names: list[str] = []
    for node in getattr(obs, "nodes", ()) or ():
        if not getattr(node, "enabled", True):
            continue
        if getattr(node, "role", "") not in _CLICKABLE_UIA_ROLES:
            continue
        nm = (getattr(node, "name", "") or "").strip()
        # Skip empty / very long (icon-only or junk) labels.
        if nm and len(nm) <= 40 and nm not in names:
            names.append(nm)
        if len(names) >= max_n:
            break
    return names


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _resolve_click_pixel(
    obj: dict[str, Any],
    monitor_geom: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Translate the model's click coordinates into an ABSOLUTE screen pixel.

    Two transforms, in this order (BUG-CU-MULTIMON + BUG-CU-NORMCOORD,
    2026-05-28 5-agent deep-dive):

    1. NORMALIZE -> PIXEL: Gemini returns coordinates on a 0..1000 grid, not
       raw pixels. Convert to monitor-local pixels via
       ``px = clamp(x, 0, 1000) / 1000 * monitor_width`` (same for y/height).
       Scaling against the MONITOR dimensions (not the screenshot image dims)
       keeps this correct even if the image is downscaled before send.
    2. ADD ORIGIN: shift by the monitor's virtual-desktop origin (left, top)
       so SetCursorPos lands on the captured monitor, not the primary one.

    Fallback: if monitor width/height are unknown (0 -- non-Windows/headless
    or a win32 failure), the model coordinates are passed through unscaled and
    only the origin (also 0,0 in that case) is added. This never raises and
    never divides by zero.
    """
    left, top, width, height = monitor_geom
    raw_x = int(obj.get("x", 0))
    raw_y = int(obj.get("y", 0))
    if width > 0 and height > 0:
        nx = min(max(raw_x, 0), _COORD_NORM_MAX)
        ny = min(max(raw_y, 0), _COORD_NORM_MAX)
        px = round(nx / _COORD_NORM_MAX * width)
        py = round(ny / _COORD_NORM_MAX * height)
        abs_x = px + left
        abs_y = py + top
        log.info(
            "[cu] coord norm=(%d,%d) -> mon_px=(%d,%d) [%dx%d] "
            "-> abs=(%d,%d)",
            raw_x, raw_y, px, py, width, height, abs_x, abs_y,
        )
        return abs_x, abs_y
    # Unknown monitor geometry: pass through (single-monitor / headless).
    return raw_x + left, raw_y + top


async def _execute_action(
    obj: dict[str, Any],
    ctx: "ComputerUseContext",
    *,
    trace_id: Any,
    user_goal: str,
    monitor_geom: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[bool, str]:
    """Run one parsed action through the tool layer.

    Returns ``(success, message)``. Terminal actions ``"done"`` and ``"fail"``
    are intercepted in :func:`run_cu_loop` BEFORE this is called; the
    defensive bottom branch only fires on a misrouted caller.

    ``monitor_geom`` is (left, top, width, height) of the monitor the
    screenshot was captured from. Click coordinates are translated from
    Gemini's 0-1000 normalized grid to an absolute screen pixel via
    :func:`_resolve_click_pixel` (BUG-CU-MULTIMON + BUG-CU-NORMCOORD).
    """
    action = obj["action"]
    tools = ctx.tools or {}
    executor = ctx.tool_executor
    if executor is None:
        return False, "tool_executor not wired"

    if action == "click":
        tool = tools.get("click")
        if tool is None:
            return False, "click tool not wired"
        abs_x, abs_y = _resolve_click_pixel(obj, monitor_geom)
        args = {
            "x": abs_x,
            "y": abs_y,
            "button": "left",
            "double": False,
        }
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, args, user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"click crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    if action == "type":
        tool = tools.get("type_text")
        if tool is None:
            return False, "type_text tool not wired"
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"text": str(obj.get("text", ""))},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"type crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    if action == "key":
        # Keyboard key/combo (e.g. Enter to submit a search) via the hotkey
        # tool, which takes a {"keys": [...]} list and supports "enter", "tab",
        # "ctrl"+letter, etc. (BUG-CU-NO-PLAN: "press Enter" was un-executable
        # before this action existed, so search flows could never submit).
        tool = tools.get("hotkey")
        if tool is None:
            return False, "hotkey tool not wired"
        keys = obj.get("keys") or []
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"keys": list(keys)},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"key crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    if action == "click_element":
        tool = tools.get("click_element")
        if tool is None:
            return False, "click_element tool not wired"
        # ClickElementTool's schema already uses "name" — pass through.
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"name": str(obj.get("name", ""))},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"click_element crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    if action == "wait":
        # In-loop pause — no tool, no screenshot, no LLM round-trip. Lets
        # the model batch ``[click_A, wait, click_B]`` so the UI between
        # clicks has time to settle without spending another plan call.
        ms = max(0, int(obj.get("ms", 0)))
        try:
            await asyncio.sleep(ms / 1000.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"wait crash: {type(exc).__name__}: {exc}"
        return True, f"waited {ms} ms"

    if action == "scroll":
        tool = tools.get("scroll")
        if tool is None:
            return False, "scroll tool not wired"
        args = {
            "direction": str(obj.get("direction", "down")),
            "amount": int(obj.get("amount", _DEFAULT_SCROLL_AMOUNT)),
        }
        # Optional region targeting: the model emits 0-1000 normalized coords
        # (same grid as click); resolve to absolute screen pixels so the wheel
        # event hits the intended window. Only when BOTH are present.
        if obj.get("x") is not None and obj.get("y") is not None:
            abs_x, abs_y = _resolve_click_pixel(obj, monitor_geom)
            args["x"], args["y"] = abs_x, abs_y
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, args, user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"scroll crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    if action == "open_app":
        tool = tools.get("open_app")
        if tool is None:
            return False, "open_app tool not wired"
        # The JSON schema uses "name" for ergonomics; the underlying tool's
        # schema requires "app_name". Adapt at the dispatch boundary so the
        # model never has to know the tool's internal key name.
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"app_name": str(obj.get("name", ""))},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_timeout_s(ctx),
            )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            return False, f"open_app crash: {type(exc).__name__}: {exc}"
        return (
            bool(getattr(res, "success", False)),
            str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
        )

    return False, f"action {action!r} reached _execute_action -- caller bug"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_cu_loop(
    task: HarnessTask,
    ctx: "ComputerUseContext",
    *,
    cancel_token: CancelToken | None = None,
) -> AsyncIterator[HarnessResult]:
    """Public entry: wrap the screenshot loop in a Jarvis-cursor session.

    The bracket swaps the OS arrow to the black-yellow Jarvis cursor at the
    very first moment of the mission (before the first screenshot) and
    restores the user's default the instant the loop exits — success,
    failure, cancel, or exception. Without this wrapper, the cursor would
    only swap on the first real mouse-move (the per-action
    ``ping_jarvis_cursor`` defence-in-depth), leaving the user staring at
    their default cursor for the 3-5 s the agent spends on its first
    screenshot + plan call.
    """
    # Breadcrumb BEFORE the cursor bracket so a future pre-loop stall is
    # attributable: if this logs but "session_bracket entered" does not, the
    # hang is the bracket; if this never logs, the hang is upstream in the
    # dispatch/bus chain (BUG-CU-STALL forensics, 2026-05-29).
    log.info("[cu] run_cu_loop entered — arming cursor bracket")
    from jarvis.overlay.system_cursor import session_bracket  # noqa: PLC0415

    async with session_bracket():
        async for chunk in _run_screenshot_loop(
            task, ctx, cancel_token=cancel_token,
        ):
            yield chunk


async def _run_screenshot_loop(
    task: HarnessTask,
    ctx: "ComputerUseContext",
    *,
    cancel_token: CancelToken | None = None,
) -> AsyncIterator[HarnessResult]:
    """Screenshot-only computer-use loop body.

    Exactly one observe -> one LLM call -> one action per step, capped at
    ``ctx.step_budget``. Yields ``HarnessResult`` chunks; the last has
    ``is_final=True``.

    The ``cancel_token`` is passed by the caller (``ComputerUseHarness``
    via ``CancelScope``); without a token the loop runs to completion but
    the kill-switch cannot interrupt it (ADR-0004).
    """
    t_start = time.time_ns()
    task_prompt = task.prompt
    history: list[str] = []
    # Effective step budget. Floored at 25 so trivial multi-step flows are
    # never cut off, and defaulted high (config ``step_budget`` default 100,
    # configurable up to 1000) so a *hard* task is not abandoned just for
    # taking many steps (user mandate 2026-05-30). This is only a final
    # backstop against a true runaway loop -- a stuck session is caught far
    # earlier by the no-progress guard (identical screenshots) and the
    # consecutive-failure cap below, so the high ceiling rarely bites.
    max_steps = max(25, int(getattr(ctx, "step_budget", 100)))
    # No-progress guard (Scrooge-anti-pattern): track the last N screenshot
    # hashes. If the loop sees the same hash _STUCK_LIMIT times in a row,
    # Gemini's clicks are landing on empty space and we bail out with a
    # clear "fail" instead of grinding through the whole budget.
    from collections import deque as _deque
    _STUCK_LIMIT = 3
    recent_hashes: _deque[str] = _deque(maxlen=_STUCK_LIMIT)
    # Consecutive-failure cap: a single failed action re-plans (fresh
    # screenshot) instead of killing the mission, but if the model fails
    # this many actions in a row we give up rather than burn the budget.
    _MAX_CONSECUTIVE_FAILURES = 4
    consecutive_failures = 0
    # Anti-oscillation + no-reopen guards (BUG-CU-TOGGLE, 2026-05-28). The
    # no-progress hash guard above only catches an UNCHANGED screen; a
    # play/pause toggle FLIPS the icon every click (screen changes), so it
    # slips past. These mission-scoped guards stop the thrash:
    #   * recent_click_targets: the last few NORMALIZED (0-1000) click points.
    #     A 2nd click within _CLICK_TOL of a recent one is a toggle-thrash --
    #     we do NOT execute it (parity-safe: leaves the system in the state
    #     produced by click #1) and force one verification re-plan instead.
    #   * opened_apps: app (lowercased) -> number of times it was launched this
    #     mission. Each app is launched AT MOST _MAX_LAUNCHES_PER_APP times
    #     (default 1 -- "einer langt", user mandate 2026-05-29). Any further
    #     open_app for the same app is SUPPRESSED with a history note pointing
    #     the model at the already-open window. This kills the window spam where
    #     a never-terminating mission re-opened the same app 4-7x (live: 7
    #     Spotify windows in one 51s mission 2026-05-28; "30 Rechner für 7+7").
    #     A GENUINE close is still handled: the regression detector below pops
    #     the entry when the foreground falls to the desktop, re-allowing ONE
    #     fresh launch -- so recover-after-close still works without spam. The
    #     old 2-step cooldown re-allowed a relaunch every 3 steps and WAS the
    #     multiplier (BUG-CU-WINDOW-SPAM, supersedes the BUG-CU-REFOCUS
    #     relaunch-to-refocus heuristic, which traded spam for re-focus).
    _CLICK_TOL = 25
    _CLICK_REPEAT_LIMIT = 2
    _MAX_LAUNCHES_PER_APP = 1
    recent_click_targets: _deque[tuple[int, int]] = _deque(maxlen=6)
    opened_apps: dict[str, int] = {}
    toggle_stop_engaged = False
    # On-demand done-verifier: set when a state-change click lands and the goal
    # looks like a play/submit/start action. The NEXT iteration runs one strict
    # judge call against the fresh screenshot before planning, so the loop stops
    # on real success instead of re-clicking a toggle.
    pending_verify = False
    # Plan-first state (BUG-CU-NO-PLAN). For a multi-step goal we generate an
    # ordered plan once (after the first screenshot) and feed it into every
    # executor turn as context so the model decomposes the task instead of
    # mashing one button. The plan is guidance, not a rigid macro; the model
    # self-tracks progress via the plan + history. ``current_step`` advances
    # heuristically as the history grows; re-planning is capped.
    plan: list[dict[str, str]] = []
    plan_attempted = False
    current_step = 0
    _MAX_REPLANS = 2
    replan_count = 0
    # Regression detection (BUG-CU-MISCLICK): track the app we expect to be in
    # the foreground so a misclick that drops us to the desktop is caught.
    expected_window_token = ""
    # Did the model actually type a search query this mission? The done-gate
    # uses this to reject the "just resumed the already-loaded track" shortcut
    # for play goals (BUG-CU-WRONG-SONG, 2026-05-29): the user asked to PLAY a
    # song, which means search + select a NEW track, not press play on whatever
    # was loaded before we started.
    typed_query = False

    def _final(stdout: str = "", stderr: str = "", exit_code: int = 0) -> HarnessResult:
        return HarnessResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=(time.time_ns() - t_start) // 1_000_000,
            is_final=True,
        )

    def _progress(msg: str) -> HarnessResult:
        return HarnessResult(stdout=msg + "\n", is_final=False)

    yield _progress(f"[cu] Start: {task_prompt[:80]}")

    for step_idx in range(1, max_steps + 1):
        if _is_cancelled(cancel_token):
            yield _final(stderr="[cu] cancelled\n", exit_code=_CANCEL_EXIT_CODE)
            return

        # Observe. Heartbeat first so a stall here is attributable in the log
        # (BUG-CU-STALL: a silent 28s do-nothing was impossible to localize).
        log.info("[cu] step %d phase=observe", step_idx)
        try:
            observation = await _observe(ctx, cancel_token)
        except asyncio.TimeoutError:
            yield _final(
                stderr=f"[cu] observe timeout (step {step_idx})\n",
                exit_code=_TIMEOUT_EXIT_CODE,
            )
            return
        except Exception as exc:  # noqa: BLE001
            yield _final(
                stderr=f"[cu] observe failed: {exc}\n",
                exit_code=_OBSERVE_EXIT_CODE,
            )
            return

        # No-progress guard: if the last _STUCK_LIMIT screenshots are
        # identical, Gemini's clicks are landing on empty space and
        # nothing on screen reacts. Bail with a clear "stuck" failure
        # instead of grinding through the rest of the budget.
        if observation.screenshot_hash:
            recent_hashes.append(observation.screenshot_hash)
            if (
                len(recent_hashes) == _STUCK_LIMIT
                and len(set(recent_hashes)) == 1
            ):
                yield _final(
                    stderr=(
                        f"[cu] no progress: {_STUCK_LIMIT} identical "
                        f"screenshots in a row at step {step_idx} -- "
                        "the click target is unreactive or off-screen.\n"
                    ),
                    exit_code=_FAIL_EXIT_CODE,
                )
                return

        # Monitor geometry for this screenshot (origin + dimensions). The
        # screenshot was just captured from the foreground monitor; clicks
        # issued this step are translated from Gemini's 0-1000 grid to
        # monitor pixels (using width/height) and shifted by the origin so
        # they land on the captured monitor, not the primary one
        # (BUG-CU-MULTIMON + BUG-CU-NORMCOORD). Computed right after observe
        # so the foreground hasn't drifted before we use it.
        monitor_geom = _capture_monitor_geometry()

        # UIA-first grounding (BUG-CU-GROUNDING): enumerate the foreground
        # window's clickable control names so the model can click_element by an
        # EXACT name (deterministic) instead of pixel-guessing a small button.
        # Empty for label-less surfaces (Spotify/games) -> the hint is omitted
        # and the loop stays pixel-first. TIGHT 3s budget (BUG-CU-STALL): a
        # wedged UIA COM call must never block the click path -- it returns []
        # and the loop proceeds with pixel grounding rather than stalling.
        log.info("[cu] step %d phase=uia", step_idx)
        control_labels = await _foreground_clickable_labels(_UIA_TIMEOUT_S)
        controls_hint = ""
        if control_labels:
            controls_hint = (
                "\n\nAVAILABLE CONTROLS (click_element by one of these EXACT "
                "names -- do NOT pixel-guess these): "
                + ", ".join(f'"{n}"' for n in control_labels)
            )

        # Plan-first: generate the ordered plan once, after the first
        # screenshot, ONLY for multi-app SEARCH goals (play/search) that truly
        # need decomposition. A compute goal ("rechne 8x8") or a simple
        # "open X + click" must NOT pay a planner round-trip on step 1 -- that
        # extra brain call was part of what blew the step-1 budget
        # (BUG-CU-STALL). On planner failure plan stays [] -> stateless loop.
        if not plan_attempted and _goal_needs_search(task_prompt):
            plan_attempted = True
            log.info("[cu] step %d phase=plan", step_idx)
            plan = await _make_plan(
                ctx, observation=observation, user_goal=task_prompt,
            )
            if plan:
                log.info(
                    "[cu] plan: %d steps -> %s",
                    len(plan), " | ".join(s["intent"][:32] for s in plan),
                )

        # Regression detector (BUG-CU-MISCLICK), conservative. We only flag a
        # regression when the foreground has clearly fallen to the DESKTOP /
        # shell -- an empty title or the Explorer shell ("Program Manager").
        # We deliberately do NOT use an "app name no longer in the title"
        # heuristic: media apps put the TRACK NAME in their window title
        # (Spotify -> "71 Digits - Low (LUNAX Remix)"), which made the old
        # check false-fire constantly (live bug 2026-05-29). The desktop/shell
        # check still catches the real "misclick closed everything" case
        # without ever tripping on a normal title change.
        win_title = (getattr(observation, "window_title", "") or "")
        wt = win_title.strip().lower()
        if (expected_window_token and step_idx > 1
                and wt in ("", "program manager", "task switching")):
            log.info(
                "[cu] REGRESSION: foreground fell to the desktop (title=%r) — "
                "last action likely closed the app", win_title,
            )
            history.append(
                f"REGRESSION: the app window is gone — the desktop is now in "
                "front. Your last click probably hit a close button. Re-open "
                "the app with open_app and resume."
            )
            opened_apps.pop(expected_window_token, None)
            expected_window_token = ""

        # On-demand done-verification (BUG-CU-TOGGLE). A state-change click
        # landed last step and the goal looks like play/submit/start -> run
        # ONE strict judge call against THIS fresh screenshot before planning
        # another action. If the goal is already achieved, finish now instead
        # of re-clicking a toggle and undoing it. Never blocks: on any error
        # the verifier returns (False, "") and the loop plans normally.
        if pending_verify:
            pending_verify = False
            # Anti-shortcut gate (BUG-CU-WRONG-SONG): for a "play a song" goal,
            # refuse to accept done until a real search query was typed this
            # mission. Otherwise the model satisfies the verifier by resuming
            # whatever track was already loaded -- a playing timer, but the
            # WRONG (not-searched-for) song.
            if _goal_needs_search(task_prompt) and not typed_query:
                log.info(
                    "[cu] done blocked: no search query typed yet — must search "
                    "for and select a track, not resume the loaded one.",
                )
                history.append(
                    "NOT DONE: you have not searched for a song yet. Click the "
                    "search box, TYPE a song/artist name, press Enter, and click "
                    "a result track. Resuming the already-loaded track does NOT "
                    "count."
                )
            else:
                done, proof = await _verify_goal_done(
                    ctx, observation=observation, user_goal=task_prompt,
                )
                if done:
                    log.info("[cu] verifier: goal achieved (proof=%r) -> done", proof[:80])
                    yield _final(
                        stdout=f"[cu] done (verified: {proof[:80]})\n", exit_code=0,
                    )
                    return
                log.info("[cu] verifier: not done yet (proof=%r)", proof[:80])
                history.append(
                    f"VERIFIER: goal NOT yet achieved ({proof[:120]}). Do NOT "
                    "repeat your last action -- if a transport/submit control "
                    "already shows the success state, emit done; otherwise try a "
                    "DIFFERENT element."
                )

        # Think. When a plan exists, feed it into the executor prompt so the
        # model knows the whole task structure and which step it is on
        # (plan-first, like Claude-in-Chrome). Without a plan, the default
        # "emit ONE JSON action" message is used (stateless reactive path).
        plan_user_message: str | None = None
        if plan:
            # Heuristically advance the current step: count completed steps as
            # roughly the number of successful actions so far, clamped.
            current_step = min(
                sum(1 for h in history if " ok " in h or " OK " in h),
                len(plan) - 1,
            )
            cur = plan[current_step]
            plan_user_message = (
                f"GOAL: {task_prompt}\n\n"
                f"PLAN:\n{_render_plan(plan, current_step)}\n\n"
                f"CURRENT STEP: {cur['intent']}\n"
                f"SUCCESS WHEN: {cur.get('success') or 'the step is visibly done'}\n\n"
                f"RECENT_STEPS:\n{chr(10).join(history[-8:]) or '(none)'}\n\n"
                "Do ONLY the current step. Emit the JSON action(s) for it. "
                "Emit {\"action\":\"done\"} ONLY when the FINAL plan step's "
                "success is visibly proven in the screenshot.\n\n"
                "SEARCH DISCIPLINE (critical): to 'play a song' you MUST search "
                "for and select a NEW track. The sequence is: click the search "
                "box, TYPE a concrete song or artist name (a real 'type' action "
                "with text -- never skip this), press Enter (key 'enter') to "
                "open the FULL results page, then click the top row under "
                "'Songs'/'Titel' (a track row -- NOT an autocomplete dropdown "
                "item, NOT a music video, podcast, or artist header). Only that "
                "starts a fresh track.\n"
                "FORBIDDEN SHORTCUT: do NOT just press the play button on "
                "whatever track was already loaded when you started -- resuming "
                "a pre-loaded song does NOT satisfy 'play a song'. If you have "
                "not yet typed a search query and clicked a result this session, "
                "you are NOT done, even if a track is already playing."
                + controls_hint
            )
        elif controls_hint:
            # No plan (simple/stateless goal) but the foreground exposes UIA
            # controls -> give the model the exact names so it click_elements
            # instead of pixel-guessing (the Calculator class of failure).
            plan_user_message = (
                f"GOAL: {task_prompt}\n"
                f"PREVIOUS_STEPS:\n{chr(10).join(history[-12:]) or '(none)'}\n\n"
                "Inspect the screenshot and emit ONE JSON action."
                + controls_hint
            )
        log.info("[cu] step %d phase=think", step_idx)
        # Wave 3 hybrid: try the native Gemini computer_use engine first when
        # enabled (ctx.native_cu). It returns loop-vocabulary actions on the
        # same 0-1000 grid, or None on ANY failure -- in which case we fall
        # through to the hand-rolled vision+JSON path below unchanged. Default:
        # ctx.native_cu is None, so this is a no-op and nothing changes.
        batch = await _decide_native_batch(
            ctx, observation, task_prompt, history, step_idx
        )
        if batch is None:
            try:
                raw = await asyncio.wait_for(
                    _call_brain(
                        ctx,
                        observation=observation,
                        user_goal=task_prompt,
                        history_text="\n".join(history[-12:]),
                        user_message=plan_user_message,
                    ),
                    timeout=_timeout_s(ctx),
                )
            except asyncio.TimeoutError:
                yield _final(
                    stderr=f"[cu] brain timeout (step {step_idx})\n",
                    exit_code=_TIMEOUT_EXIT_CODE,
                )
                return
            except Exception as exc:  # noqa: BLE001
                yield _final(
                    stderr=f"[cu] brain failed (step {step_idx}): {exc}\n",
                    exit_code=_PARSE_EXIT_CODE,
                )
                return

            # Parse — model may return a single action object OR a list of
            # action objects (a batch). Both shapes are validated and normalised
            # to a list, so the executor below iterates uniformly.
            try:
                batch = _parse_actions(raw)
            except CULoopError as exc:
                yield _final(
                    stderr=f"[cu] parse (step {step_idx}): {exc}\n",
                    exit_code=_PARSE_EXIT_CODE,
                )
                return

        if len(batch) > 1:
            log.info(
                "[cu] step %d batch size = %d (plan-then-execute)",
                step_idx, len(batch),
            )

        # Batch executor — runs the whole list under ONE screenshot. A
        # ``done`` or ``fail`` ends the mission immediately; any other
        # action failure breaks the batch and falls back to the outer
        # loop for a fresh screenshot + re-plan.
        for batch_idx, action_obj in enumerate(batch, start=1):
            action = action_obj["action"]
            tag = f"step {step_idx}.{batch_idx}"

            # Mid-batch cancel check (BUG-CU-HANGUP): "auflegen" cancels the
            # CU token; honour it between batch items so we stop within ~1
            # in-flight action instead of running the whole batch out.
            if _is_cancelled(cancel_token):
                yield _final(
                    stderr="[cu] cancelled mid-batch\n",
                    exit_code=_CANCEL_EXIT_CODE,
                )
                return

            # Per-action log line — visible in data/jarvis_desktop.log so
            # an operator can trace the exact action sequence after the
            # fact (the model's reasoning is silent otherwise).
            log.info(
                "[cu] %s action=%s args=%s",
                tag, action,
                {k: v for k, v in action_obj.items() if k != "action"},
            )

            # Per-mission launch cap (BUG-CU-WINDOW-SPAM, 2026-05-29): each app
            # is launched AT MOST _MAX_LAUNCHES_PER_APP times (default 1 — "einer
            # langt"). Any further open_app for the same app is suppressed with a
            # history note pointing the model at the already-open window, instead
            # of spawning a duplicate. A genuine close still re-allows ONE launch
            # via the regression detector above (it pops opened_apps when the
            # foreground falls to the desktop). This supersedes the old
            # relaunch-every-3-steps cooldown that re-opened the same app 4-7×
            # per never-terminating mission (live: 7 Spotify windows in 51s).
            if action == "open_app":
                _app = str(action_obj.get("name", "")).strip().lower()
                _launches = opened_apps.get(_app, 0)
                if _app and _launches >= _MAX_LAUNCHES_PER_APP:
                    log.info(
                        "[cu] %s open_app %r SUPPRESSED — already launched %d× "
                        "this mission (cap %d); its window is open, interact with "
                        "it instead of relaunching",
                        tag, _app, _launches, _MAX_LAUNCHES_PER_APP,
                    )
                    history.append(
                        f"{tag}: open_app {_app} SKIPPED — {_app} was already "
                        f"launched this mission and its window is open. Click it "
                        f"(or its taskbar button) to focus it, or interact with "
                        f"the visible window. Do NOT call open_app for {_app} "
                        f"again."
                    )
                    consecutive_failures = 0
                    continue
                if _app:
                    opened_apps[_app] = _launches + 1
                    # Remember the app so the regression detector can notice if a
                    # later misclick drops us to the desktop (BUG-CU-MISCLICK); on
                    # a genuine close it pops this entry, re-allowing ONE fresh
                    # launch so recover-after-close still works.
                    expected_window_token = _app

            # Repeated-click / toggle-thrash guard (BUG-CU-TOGGLE): a 2nd click
            # within _CLICK_TOL of a recent click target is a toggle thrash
            # (play/pause flips the icon every click, so the no-progress hash
            # guard never trips). Do NOT execute the repeat -- breaking BEFORE
            # _execute_action is parity-safe: the system stays in the state
            # produced by click #1 ("playing"). Force one verification re-plan.
            if action == "click":
                _tx = int(action_obj.get("x", -999))
                _ty = int(action_obj.get("y", -999))
                _near = sum(
                    1 for (px, py) in recent_click_targets
                    if abs(px - _tx) <= _CLICK_TOL and abs(py - _ty) <= _CLICK_TOL
                )
                recent_click_targets.append((_tx, _ty))
                if _near >= _CLICK_REPEAT_LIMIT:
                    toggle_stop_engaged = True
                    log.info(
                        "[cu] %s repeated click ~(%d,%d) x%d — toggle-stop "
                        "(not executed)", tag, _tx, _ty, _near,
                    )
                    break

            # Telemetry — swallowed on failure to protect the loop.
            if ctx.bus is not None:
                try:
                    target_hint = json.dumps(
                        {k: v for k, v in action_obj.items() if k != "action"},
                    )[:80]
                    await ctx.bus.publish(ActionPlanned(
                        action_kind=action, target_hint=target_hint,
                    ))
                except Exception:  # noqa: BLE001
                    log.debug("ActionPlanned publish failed", exc_info=True)

            # Terminal actions short-circuit the entire mission.
            if action == "done":
                # Compute goals: a model-emitted ``done`` must be checked
                # against the actual result on screen, not taken on faith
                # (BUG-CU-RESULT: Calc showed 130, model claimed done). Read
                # the display and verify it equals the computed answer; on a
                # mismatch, refuse to finish and inject a correction so the
                # loop fixes it. Non-compute goals finish as before.
                if _goal_needs_result(task_prompt):
                    ok, proof = await _verify_goal_done(
                        ctx, observation=observation, user_goal=task_prompt,
                    )
                    if not ok:
                        log.info(
                            "[cu] %s done REJECTED — result not verified (%s)",
                            tag, proof[:80],
                        )
                        history.append(
                            f"RESULT NOT CONFIRMED ({proof[:120]}). The "
                            "calculator does not show the correct answer yet. "
                            "Clear it (press 'Escape' or click 'C') and re-enter "
                            "the calculation using click_element on the named "
                            "digit/operator keys, then press 'Gleich'."
                        )
                        break  # re-plan from a fresh screenshot
                    log.info("[cu] %s done verified: %s", tag, proof[:80])
                yield _final(
                    stdout=f"[cu] done at {tag}\n", exit_code=0,
                )
                return
            if action == "fail":
                reason = (
                    str(action_obj.get("reason", "")).strip()
                    or "model declined"
                )
                yield _final(
                    stderr=f"[cu] fail at {tag}: {reason}\n",
                    exit_code=_FAIL_EXIT_CODE,
                )
                return

            yield _progress(
                f"[cu] {tag}: {action} "
                f"{{x={action_obj.get('x', '-')}, y={action_obj.get('y', '-')}, "
                f"text={action_obj.get('text', '-')!r}, "
                f"name={action_obj.get('name', '-')!r}, "
                f"ms={action_obj.get('ms', '-')}}}"
            )

            # Act.
            try:
                success, message = await _execute_action(
                    action_obj, ctx,
                    trace_id=observation.trace_id, user_goal=task_prompt,
                    monitor_geom=monitor_geom,
                )
            except asyncio.TimeoutError:
                yield _final(
                    stderr=f"[cu] action timeout at {tag}\n",
                    exit_code=_TIMEOUT_EXIT_CODE,
                )
                return

            history.append(
                f"{tag}: {action} {'ok' if success else 'FAIL'} ({message[:60]})",
            )

            if not success:
                # BUG-CU fix 2026-05-28: do NOT kill the whole mission on a
                # single action miss (e.g. a click_element that found no
                # matching UIA node, or a click that hit nothing). Feed the
                # failure into history, break the batch, and let the OUTER
                # loop take a FRESH screenshot and re-plan. The model sees
                # the failure note next turn and can try a different target.
                # Total attempts stay bounded by max_steps + the no-progress
                # guard + the consecutive-failure cap below.
                consecutive_failures += 1
                log.info(
                    "[cu] %s action %r failed (re-planning, %d in a row): %s",
                    tag, action, consecutive_failures, message[:80],
                )
                yield _progress(
                    f"[cu] {tag} failed: {message[:60]} -- re-planning"
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    # Before giving up: if we have a plan and re-plan budget,
                    # re-plan from the CURRENT screen (keeping what worked) and
                    # keep going -- a stuck step often just needs a fresh plan
                    # from the new on-screen state (BUG-CU-NO-PLAN re-plan).
                    if plan and replan_count < _MAX_REPLANS:
                        replan_count += 1
                        consecutive_failures = 0
                        log.info(
                            "[cu] re-planning from current screen (#%d/%d) after "
                            "repeated failures", replan_count, _MAX_REPLANS,
                        )
                        try:
                            obs2 = await _observe(ctx, cancel_token)
                            new_plan = await _make_plan(
                                ctx, observation=obs2, user_goal=task_prompt,
                            )
                            if new_plan:
                                plan = new_plan
                                history.append(
                                    f"RE-PLANNED ({replan_count}): new plan has "
                                    f"{len(plan)} steps."
                                )
                        except Exception:  # noqa: BLE001
                            pass
                        break
                    yield _final(
                        stderr=(
                            f"[cu] giving up after {consecutive_failures} "
                            f"consecutive action failures (last: {message[:80]})\n"
                        ),
                        exit_code=_TOOL_EXIT_CODE,
                    )
                    return
                break  # exit batch, fall through to next outer step (fresh screenshot)
            # A successful action resets the consecutive-failure streak.
            consecutive_failures = 0
            # Remember that a real search query was typed this mission -- the
            # done-gate uses this to reject "resumed the already-loaded track"
            # for play goals (BUG-CU-WRONG-SONG, 2026-05-29).
            if action == "type" and str(action_obj.get("text", "")).strip():
                typed_query = True
            # Arm the on-demand done-verifier after a state-change click on a
            # play/submit/start-type goal: the NEXT iteration judges the fresh
            # screenshot before planning another (possibly toggle-undoing)
            # action (BUG-CU-TOGGLE).
            if action in ("click", "click_element") and _goal_needs_verification(task_prompt):
                pending_verify = True

        # End of batch. If the repeated-click guard engaged, inject a
        # constrained directive so the next turn VERIFIES instead of clicking
        # the same control again (parity-safe: the offending repeat click was
        # never executed, so we remain in the click-#1 state).
        if toggle_stop_engaged:
            toggle_stop_engaged = False
            pending_verify = pending_verify or _goal_needs_verification(task_prompt)
            history.append(
                "GUARD: you clicked the same control repeatedly. Clicking a "
                "play/pause or other toggle again UNDOES your work. Look at the "
                "CURRENT screenshot: if the success proof is visible (e.g. a "
                "pause glyph + progress past 0:00), emit {\"action\":\"done\"}. "
                "Only if it is clearly NOT achieved, try a DIFFERENT element. "
                "Do NOT click the same control again."
            )

    yield _final(
        stderr=f"[cu] step budget {max_steps} exhausted\n",
        exit_code=_BUDGET_EXIT_CODE,
    )
