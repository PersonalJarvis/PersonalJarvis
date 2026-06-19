"""Screenshot-Only Computer-Use Loop (POAV simplification, 2026-05-26).

One screenshot -> one LLM call -> ONE OR MORE actions per step (the latter
when the model returns a batch; see below). No Set-of-Marks, no UIA tree,
no replan budget, no verify-after-step pass.

Action schema (the model returns either a single object OR a list of
objects per turn -- the executor handles both):

    {"action": "click_element", "name": "<UIA label>"}   UIA-grounded click
    {"action": "click",         "x": <int>, "y": <int>,
                                "target": "<element>"}   0-1000 normalized coords
                                                         (target arms the zoom-
                                                         refinement pass)
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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.events import (
    ActionPlanned,
    AnnouncementRequested,
    CUStepProfiled,
    ObservationCaptured,
)
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

# Guard-hit cap (live failure 2026-06-10 20:46): a mission that keeps
# producing guard-BLOCKED actions (suppressed relaunches, repeated-click
# toggle-stops) is circling — the model has lost the thread and no longer
# finds a productive action. Pre-fix such a run ground through the whole
# step/time budget (8x suppressed open_app + 3x toggle-stop over 2 minutes,
# exit 4). The counter is CUMULATIVE per mission (no reset on a success in
# between): guard hits are symptoms of disorientation, and the live run
# interleaved useless-but-"ok" clicks between them.
_MAX_GUARD_HITS: int = 5

# Exit codes — kept stable for callers that branch on them (voice/UI layer).
_TIMEOUT_EXIT_CODE = 124
_FAIL_EXIT_CODE = 5
_BUDGET_EXIT_CODE = 4
_PARSE_EXIT_CODE = 2
_TOOL_EXIT_CODE = 8
_OBSERVE_EXIT_CODE = 1
_CANCEL_EXIT_CODE = 130

# Fail-gate reject budget (completion-enforcement, 2026-06-15). A voluntary
# ``fail`` is the SYMMETRIC sibling of ``done``: it must survive the strict
# feasibility judge (``_verify_fail_justified``) before it ends the mission,
# exactly as ``done`` must survive the completion judge. This closes the
# reward-hack where quitting was free while succeeding was judge-gated — a weak
# model under friction took the free exit even with the goal nearly achieved
# (live 2026-06-15 Snipping-Tool turn: emitted ``fail`` with the capture overlay
# already on screen). Each rejected fail costs ONE re-plan; the bound guarantees
# a genuinely impossible task still terminates ("verified-impossible after N").
# Kept module-level (sibling of the exit codes) so it is tunable + test-visible.
_MAX_FAIL_REJECTS: int = 2

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
    "  {\"action\": \"click\",         \"x\": <int>, \"y\": <int>, "
    "\"target\": \"<2-6 words: the element you aim at>\"}\n"
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
    "* ALWAYS include \"target\" on a pixel click (e.g. \"skip forward "
    "button\"): a zoomed verification pass uses it to re-locate the exact "
    "element and silently corrects your coordinates before clicking.\n"
    "* To type into a field: first focus it (click_element the field, or "
    "click it), then ``type``. Never type blindly into an unfocused screen.\n"
    "* LITERAL DICTATION: when the goal tells you to type, say, write, or enter "
    "specific words (e.g. 'type hello hello hello', 'say X', 'write Y'), the "
    "``type`` action's text MUST be exactly those words -- copy them verbatim. "
    "Do NOT add, wrap, or transform them into a shell command or any prefix; in "
    "particular NEVER prepend 'echo' or surround them with quotes. Only compute "
    "different text when the goal explicitly asks you to (e.g. 'search for X' -> "
    "type the query X; 'go to gmail' -> type the URL).\n"
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
    "\"chrome\", \"edge\", \"explorer\", \"cmd\", \"discord\", \"slack\".\n"
    "* PREFER THE INSTALLED DESKTOP APP over a browser. When the goal names an "
    "app that has a native desktop client (Discord, Slack, Spotify, Telegram, "
    "WhatsApp, Steam, ...), open it with ``open_app`` -- do NOT open it inside "
    "a web browser and do NOT navigate a browser to its website. Only fall "
    "back to the web version when the goal explicitly says 'in the browser' / "
    "'web version', or when a desktop launch clearly did not work (the app is "
    "not installed). open_app already resolves the desktop client across "
    "platforms; if it is unavailable it reports so and you can then try the "
    "browser.\n"
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
    "  A \"fail\" is VERIFIED against the current screen: if the goal still\n"
    "  looks achievable your fail is REJECTED and you must keep working --\n"
    "  do NOT use \"fail\" to escape a hard-but-doable task.\n"
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

def _normalize_click_target(obj: dict[str, Any]) -> None:
    """Keep an optional ``target`` description on a click action, drop junk.

    The zoom-refinement stage (2026-06-10 click-accuracy fix) uses the
    description to re-locate the element inside a zoomed crop. It is
    best-effort metadata: a missing or malformed value must never fail the
    action, so anything that is not a non-empty string is silently removed.
    """
    target = obj.get("target")
    if isinstance(target, str) and target.strip():
        obj["target"] = target.strip()
    elif "target" in obj:
        obj.pop("target", None)


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
        _normalize_click_target(obj)
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
        _normalize_click_target(obj)
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
_UIA_TIMEOUT_S = 3.0
#: Screenshot capture+encode budget (2026-06-09 Wave 0: per-phase budgets
#: replace the old silent 12s `_PER_OP_TIMEOUT_CAP_S` blanket). Measured worst
#: case on a 4K monitor is ~1.05s; 3s leaves headroom without letting a
#: wedged GDI call eat the step.
_OBSERVE_TIMEOUT_S = 3.0
#: Single tool execution (click/type/open_app/...). App launches are the slow
#: end; anything beyond this is a wedged tool, not a slow action.
_ACT_TIMEOUT_S = 5.0
#: Brief pause before a ``type`` action so a freshly-focused webview/Tauri text
#: input (e.g. the BridgeSpace terminal) is actually listening before keystrokes
#: arrive. Without it the focusing click and the type land within ~2 ms and the
#: first characters are dropped (CU typo bug 2026-06-15).
_PRE_TYPE_SETTLE_S = 0.15
#: Model-call ceiling (think/plan/judge). The configured per_step_timeout_s
#: still applies when SMALLER; this cap bounds a hung provider call.
_THINK_TIMEOUT_CAP_S = 10.0


def _think_timeout_s(ctx: ComputerUseContext) -> float:
    """Model-call timeout: the configured per_step value, capped at the named
    think ceiling (no more silent blanket cap over every phase)."""
    cfg_v = float(getattr(ctx, "per_step_timeout_s", 30.0) or 30.0)
    return max(0.001, min(cfg_v, _THINK_TIMEOUT_CAP_S))


def _internal_deadline_s(timeout_s: float) -> float:
    """Loop-internal mission budget: end cleanly BEFORE the harness guillotine
    (``ComputerUseHarness.invoke`` wraps the stream in ``asyncio.wait_for``).
    90% of the outer budget, floored so tiny test budgets stay positive."""
    return max(5.0, float(timeout_s) * 0.9)


def _is_cancelled(cancel_token: CancelToken | None) -> bool:
    return bool(cancel_token is not None and cancel_token.is_cancelled())


#: Minimum gap between spoken mid-mission progress announcements — milestones,
#: not narration (frontier-speed Wave 0).
_PROGRESS_MIN_INTERVAL_S = 8.0

#: Strong refs for fire-and-forget announcement publishes. ``bus.publish``
#: awaits TYPED subscribers uncapped (the AP-18 timeout covers only wildcard
#: observers) and ``SpeechPipeline._on_announcement`` synthesizes TTS inline —
#: awaiting the publish therefore froze the CU loop 6-10 s per spoken
#: milestone (BUG-CU-ANNOUNCE-BLOCK, live log 2026-06-10 20:46: every step
#: gap ended exactly at AudioOutFirst). The loop detaches every announcement
#: publish instead; the set keeps the tasks alive until done.
_ANNOUNCE_TASKS: set[asyncio.Task[None]] = set()


def _publish_announcement_nonblocking(bus: Any, event: Any) -> None:
    async def _run() -> None:
        try:
            await bus.publish(event)
        except Exception:  # noqa: BLE001
            log.debug("announcement publish failed", exc_info=True)

    task = asyncio.create_task(_run(), name="cu-announce")
    _ANNOUNCE_TASKS.add(task)
    task.add_done_callback(_ANNOUNCE_TASKS.discard)


#: Settle probe after a successful open_app (2026-06-10 latency plan Task 6).
#: open_app is a fire-and-forget Popen — observing immediately catches the
#: pre-launch desktop and burns a full observe+think round (~3-5 s) on a
#: stale frame. Poll the cheap foreground-title hint until the app's window
#: is up, then observe.
_OPEN_APP_SETTLE_TIMEOUT_S = 3.0
_OPEN_APP_SETTLE_POLL_S = 0.3


async def _settle_after_open_app(ctx: ComputerUseContext, app_token: str) -> None:
    """Wait (max ``_OPEN_APP_SETTLE_TIMEOUT_S``) until the freshly launched
    app's window is in the foreground.

    The probe is the vision engine's foreground-title hint (a ctypes
    GetForegroundWindow read — microseconds, no screenshot, no UIA walk).
    Structural seam: any engine exposing ``_guess_active_app_hint`` works;
    fakes without it (and engines on platforms whose probe returns "") cost
    one short settle beat at most. Never raises."""
    if not app_token:
        return
    probe = getattr(
        getattr(ctx, "vision_engine", None), "_guess_active_app_hint", None,
    )
    if probe is None:
        return
    deadline = time.monotonic() + _OPEN_APP_SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            title = str(await asyncio.to_thread(probe, None) or "")
        except Exception:  # noqa: BLE001
            log.debug("[cu] settle probe failed (non-fatal)", exc_info=True)
            return
        if not title:
            # No title available (empty desktop focus, or a platform whose
            # probe returns "") — one fixed settle beat instead of a blind
            # poll-until-timeout.
            await asyncio.sleep(min(1.0, _OPEN_APP_SETTLE_TIMEOUT_S / 3))
            return
        if app_token in title.lower():
            return
        await asyncio.sleep(_OPEN_APP_SETTLE_POLL_S)


async def _profile_phase(
    ctx: ComputerUseContext, *, phase: str, step_idx: int, t0: float,
    acc: dict[str, float] | None = None,
) -> None:
    """Publish one CUStepProfiled phase span (Wave 0 instrumentation).

    Dual purpose: cu_bench latency breakdown AND the speech-pipeline liveness
    heartbeat (a long THINK phase emits no ObservationCaptured/ActionPlanned,
    so this event keeps the TTS ceiling suspended). Never raises.

    ``acc`` (latency plan Task 7): per-mission phase accumulator — feeds the
    one-line ``[cu] mission profile`` summary every ``_final`` emits.
    Accumulated before the bus gate so the profile works without a bus too.
    """
    if acc is not None:
        acc[phase] = acc.get(phase, 0.0) + (time.monotonic() - t0) * 1000.0
    if ctx.bus is None:
        return
    try:
        await ctx.bus.publish(CUStepProfiled(
            phase=phase,  # type: ignore[arg-type]
            duration_ms=max(0, int((time.monotonic() - t0) * 1000)),
            step_idx=step_idx,
        ))
    except Exception:  # noqa: BLE001
        log.debug("CUStepProfiled publish failed", exc_info=True)


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
    ctx: ComputerUseContext,
    cancel_token: CancelToken | None,
) -> Observation:
    """Capture one screenshot and emit ObservationCaptured.

    Mode is explicitly ``screenshot`` (2026-06-09 latency fix): the loop
    never reads ``observation.nodes`` -- clickable labels come from the
    separate ``_foreground_clickable_labels`` enumeration -- so the old
    ``auto`` mode paid a full composite UIA enumeration per step for
    nothing. The window title still arrives via the engine's foreground
    probe (BUG-CU-EMPTYTITLE fix in jarvis/vision/engine.py).
    """
    obs = await asyncio.wait_for(
        ctx.vision_engine.observe(mode="screenshot", cancel_token=cancel_token),
        timeout=_OBSERVE_TIMEOUT_S,
    )
    if obs is None:
        # Transient GDI/BitBlt failure (locked screen, display asleep) — fail
        # with a clear message instead of an AttributeError downstream.
        raise CULoopError("screenshot capture returned no frame (transient GDI failure)")
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

# Per-image byte budget for the model payload (2026-06-09 latency fix). The
# loop used to ship the raw full-resolution screenshot (a 4K monitor every
# step) -- encode + upload + model ingest paid for pixels the vision models
# resample away anyway (~1568px internally). ``cap_image_b64`` downscales to
# 2048px longest side and JPEG-encodes toward this budget; on any failure it
# returns the original image, so the vision path never breaks.
_CU_IMAGE_MAX_BYTES = 300_000


async def _load_observation_image(obs: Observation) -> ImageBlock | None:
    """Read the observation's screenshot and cap it for the model payload.

    Returns ``None`` when the observation has no screenshot on disk. Raises
    on unreadable files -- callers treat that like the previous read failure
    (log + skip the image).
    """
    if not obs.screenshot_path:
        return None
    from jarvis.brain.router import _read_observation_image_b64  # noqa: PLC0415
    from jarvis.vision.image_budget import cap_image_b64  # noqa: PLC0415

    mime, image_b64 = await _read_observation_image_b64(obs)
    mime, image_b64 = cap_image_b64(mime, image_b64, _CU_IMAGE_MAX_BYTES)
    return ImageBlock(mime=mime, data_b64=image_b64, source_hash=obs.screenshot_hash)

async def _call_brain(
    ctx: ComputerUseContext,
    *,
    observation: Observation,
    user_goal: str,
    history_text: str,
    system_prompt: str | None = None,
    user_message: str | None = None,
    frame_b: Observation | None = None,
    max_tokens: int = 256,
    images_override: list[ImageBlock] | None = None,
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

        # Attach screenshot(s), capped to the model-payload budget
        # (2026-06-09 latency fix). ``frame_b`` lets the two-frame motion
        # verifier send Frame A + Frame B in one call for comparison.
        # ``images_override`` replaces the observation frames entirely — the
        # click-refinement pass sends a zoomed live crop instead of the full
        # (and by now stale) step screenshot.
        images: list[ImageBlock] = []
        if images_override is not None:
            images = list(images_override)
        else:
            for obs in (observation, frame_b):
                if obs is None or not obs.screenshot_path:
                    continue
                try:
                    block = await _load_observation_image(obs)
                    if block is None:
                        continue
                    images.append(block)
                    log.info(
                        "ComputerUseLoop screenshot attached: hash=%s len=%d",
                        obs.screenshot_hash[:16] if obs.screenshot_hash else "?",
                        len(block.data_b64),
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

    raise CULoopError(
        "BrainManager exposes neither complete_text, _get_brain, nor __call__"
        " -- screenshot-only loop cannot dispatch."
    )


async def _decide_native_batch(
    ctx: ComputerUseContext,
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
            timeout=_think_timeout_s(ctx),
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


# Multi-step goals beyond music (2026-06-09 shippability fix): a compound
# command ("oeffne X und ...", "... dann ...") or an explicit navigation goal
# needs the ordered plan just as much as a play goal -- the reactive loop
# loses the thread on them. Conservative connectives only; a single-verb goal
# ("mach einen Screenshot") stays on the cheap stateless path.
_MULTI_STEP_GOAL_RE = re.compile(
    r"\bund\b|\bdann\b|\bdanach\b|\banschliessend\b|"
    r"\bnavigier\w*\b|\bnavigate\b|"
    r"\band\s+(?:go|open|click|navigate|type|search)\b|\bthen\b",
    re.I,
)


def _goal_needs_plan(goal: str) -> bool:
    """True when the goal benefits from an ordered plan: music/search goals
    (the original plan-first class) plus any compound or navigation goal.
    Compute goals (calculator) never plan -- their connectives ("rechne 7
    und 3") are part of the arithmetic, not a step sequence, and the
    stateless path is faster (review finding 2026-06-09)."""
    if _goal_needs_result(goal):
        return False
    return _goal_needs_search(goal) or bool(_MULTI_STEP_GOAL_RE.search(goal or ""))


# Anti-shortcut block for music/search goals (BUG-CU-WRONG-SONG). Shared by
# the plan-path prompt AND the VERIFY-FIRST fallback prompt so a failed
# planner can never silently drop the discipline (review finding 2026-06-09).
_SEARCH_DISCIPLINE_BLOCK = (
    "\n\nSEARCH DISCIPLINE (critical): to 'play a song' you MUST search "
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
)

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


# Generic single-frame completion judge (2026-06-09 shippability fix). Every
# goal class that is neither compute (calculator display check) nor a media
# toggle (two-frame motion check) is judged against the CURRENT screenshot:
# "open Chrome" must show an open Chrome window, not the word "chrome" typed
# into a search box. Single-frame keeps the added latency to one model call
# at mission end -- no 1.3 s two-frame gap for goals that do not need motion.
_GENERIC_VERIFIER_SYSTEM_PROMPT = (
    "You are a STRICT completion judge for a desktop automation task. Look "
    "at the screenshot and decide whether the user's GOAL is OBSERVABLY "
    "achieved RIGHT NOW. Output exactly ONE JSON object, no prose, no code "
    "fences: {\"done\": true|false, \"proof\": \"<the exact on-screen "
    "evidence you used>\"}\n"
    "Rules:\n"
    "* done:true ONLY if the screenshot PROVES the goal. For an 'open <app>' "
    "goal that means the app's window is visibly OPEN -- the app's name "
    "typed into a search box or shown in a start-menu result list is NOT "
    "enough. For a navigation goal the target page/screen must be visible.\n"
    "* If the goal has multiple parts (open X AND do Y), ALL parts must be "
    "proven on screen.\n"
    "* Never guess. When unsure, answer false.\n"
    "* Quote the concrete proof element (window, title, page content) in "
    "'proof'."
)


# Feasibility judge for the ``fail`` action (completion-enforcement, 2026-06-15).
# The SYMMETRIC sibling of the completion judge: when the agent wants to GIVE UP,
# this decides whether the goal is genuinely impossible from HERE — or still
# achievable with more actions. The burden of proof is on IMPOSSIBILITY, so the
# default is keep-working; "it's hard" / "I tried twice" is not impossible. This
# is what removes the cheap give-up exit (the recorded reward-hack).
_FAIL_VERIFIER_SYSTEM_PROMPT = (
    "You are a STRICT feasibility judge for a desktop automation task. The "
    "automation agent wants to GIVE UP on the user's GOAL. Look at the "
    "screenshot and decide whether the goal is genuinely IMPOSSIBLE or BLOCKED "
    "from the CURRENT screen, or whether it is still achievable with more "
    "actions. Output exactly ONE JSON object, no prose, no code fences: "
    "{\"give_up\": true|false, \"reason\": \"<the exact on-screen evidence you "
    "used>\"}\n"
    "Rules:\n"
    "* give_up:true ONLY if the screenshot PROVES the goal cannot be reached "
    "from here -- a hard error/permission dialog, a missing capability, or a "
    "required element that exists NOWHERE reachable on screen.\n"
    "* The agent's stated reason is a CLAIM, not proof. Trust the screenshot, "
    "not the claim. 'It is hard', 'unclear', 'I tried a couple times', or a "
    "control simply not found yet is NOT impossible -> give_up:false.\n"
    "* If ANY visible element could plausibly advance the goal (a button, a "
    "field, a menu, a list row, a search box), the task is still achievable "
    "-> give_up:false.\n"
    "* When in ANY doubt, answer give_up:false. The default is to KEEP WORKING.\n"
    "* Quote the concrete on-screen evidence (the blocking dialog, or the "
    "element that still makes the goal reachable) in 'reason'."
)


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


#: Matches goals that are NOTHING but "open <app>" (any common conjugation /
#: politeness filler, DE+EN). Used for the deterministic done check below —
#: a non-match simply falls through to the LLM judge, so this regex only has
#: to be precise, never complete.
_OPEN_GOAL_RE = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,\s]+)?"
    r"(?:oeffne|öffne|öffnest|offne|starte|open|start|launch)\s+"  # i18n-allow: German voice-command pattern
    r"(?:mir\s+|mal\s+|bitte\s+|kurz\s+|einmal\s+|den\s+|die\s+|das\s+|der\s+"  # i18n-allow: German voice-command pattern
    r"|my\s+|the\s+|f(?:ue|ü)r\s+mich\s+)*"  # i18n-allow: German voice-command pattern
    r"(?P<app>[\w .-]{2,40}?)"
    r"\s*(?:f(?:ue|ü)r\s+mich|bitte|mal|kurz|jetzt|now|please)?\s*[.!?]?\s*$",  # i18n-allow: German voice-command pattern
    re.IGNORECASE,
)


def _open_goal_app_token(task_prompt: str) -> str | None:
    """The app name when the WHOLE goal is just "open <app>", else ``None``."""
    m = _OPEN_GOAL_RE.match((task_prompt or "").strip())
    if not m:
        return None
    token = m.group("app").strip().lower()
    return token or None


# ---------------------------------------------------------------------------
# Deterministic screenshot fast path (over-execution fix, 2026-06-16)
# ---------------------------------------------------------------------------
# A bare "take a screenshot" goal is a ONE-SHOT capture, not a GUI-exploration
# task. Routing it through this vision loop made a weak fast-tier model fight the
# Snipping Tool for 17 steps / 38 s (live turn 2026-06-16 15:14), clicking
# "Neuer Screenshot" three times — each click DISCARDS the prior capture, which
# is exactly the "deleted three screenshots" the user reported — before it
# recognised completion. The loop only stops when the model volunteers ``done``;
# it never asks "is the goal already satisfied?". So we satisfy the capture
# directly and never enter the loop. Mirrors the gate's screenshot classifier
# but anchored to the WHOLE goal so a compound ("screenshot AND send it") still
# runs the loop.

#: The screenshot noun (DE+EN). A goal with none of these is never a capture.
_SCREENSHOT_NOUN_RE = re.compile(
    r"\b(?:screen\s*shots?|bildschirm(?:fotos?|aufnahmen?|abz(?:ug|uege)))\b",  # i18n-allow
    re.IGNORECASE,
)
#: A "capture it NOW" intent verb (DE+EN). "machen/nimm/erstell/knips" + the
#: English imperatives; "do/get" cover the noisy STT phrasing the live turn
#: produced ("I do a screenshot from my screen right now").
_SCREENSHOT_TAKE_RE = re.compile(
    r"\b(?:mach(?:e|st|en)?|nimm(?:st)?|erstell\w*|knips\w*|"  # i18n-allow: German screenshot verbs
    r"take|taking|grab|capture|snap|do|get|gib|gimme)\b",
    re.IGNORECASE,
)
#: Intents that are NOT a fresh capture: send / share / show / open / mail a
#: screenshot, or refer to a previous / saved / already-made one. Any of these
#: keeps the goal off the fast path (the gate already excludes most, but this is
#: the loop's own belt-and-braces).
_SCREENSHOT_NOT_TAKE_RE = re.compile(
    r"\b(?:send|schick\w*|share|teil\w*|show|zeig\w*|open|oeffne|öffne|"  # i18n-allow
    r"email|mail|upload|post|"
    r"last|letzt\w*|previous|vorherig\w*|recent|"  # i18n-allow: German "last/previous"
    r"saved|gespeichert\w*|already|schon|gemacht)\b",  # i18n-allow: German "saved/already/made"
    re.IGNORECASE,
)
#: A compound goal — a screenshot PLUS a follow-up action joined by und/and/then.
#: The capture half is real but the loop (or a later turn) must do the rest, so
#: we do NOT short-circuit it.
_SCREENSHOT_COMPOUND_RE = re.compile(
    r"\b(?:und|and|then|dann|danach|anschliessend|afterwards?)\b\s+\w",  # i18n-allow
    re.IGNORECASE,
)


def _is_pure_screenshot_goal(goal: str) -> bool:
    """True when the WHOLE goal is just "take a screenshot" (DE+EN).

    Requires a screenshot noun AND a capture-it-now verb, and rejects
    send/show/last-screenshot intents and compound goals. Deliberately precise
    over complete: a non-match simply runs the normal loop (no regression)."""
    g = (goal or "").strip()
    if not g:
        return False
    if not _SCREENSHOT_NOUN_RE.search(g):
        return False
    if not _SCREENSHOT_TAKE_RE.search(g):
        return False
    if _SCREENSHOT_NOT_TAKE_RE.search(g):
        return False
    if _SCREENSHOT_COMPOUND_RE.search(g):
        return False
    return True


def _user_screenshot_dir() -> Path:
    """User-facing folder for a saved screenshot: ``~/Pictures/Screenshots``
    when a Pictures folder exists (Windows/macOS/most Linux), else
    ``~/Screenshots``. NOT the repo dev-capture folder (that one auto-prunes)."""
    home = Path.home()
    pics = home / "Pictures"
    base = pics if pics.is_dir() else home
    return base / "Screenshots"


def _save_user_screenshot() -> Path | None:
    """Capture the active monitor and save it as a PNG the user can find.

    The deterministic fulfilment of a bare "take a screenshot" goal — the same
    mss + PIL capture the ``screenshot`` vision tool uses, but written to disk
    for the user instead of handed to the brain as a vision artifact. Returns the
    saved path, or ``None`` on ANY failure (desktop extras absent, headless VPS,
    a capture error) so the caller falls through to the interactive loop with no
    regression."""
    try:
        import mss  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        from jarvis.vision.screenshot import select_capture_monitor  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — desktop extras absent (cloud-first base install)
        return None
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            if len(monitors) < 2:
                return None
            target = select_capture_monitor(monitors, strategy="foreground")
            raw = sct.grab(target)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        dest_dir = _user_screenshot_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = dest_dir / f"Screenshot_{stamp}.png"
        image.save(str(path), format="PNG")
        return path
    except Exception:  # noqa: BLE001 — mss/PIL/display errors are diverse
        log.debug("[cu] deterministic screenshot capture failed", exc_info=True)
        return None


async def _verify_goal_done(
    ctx: ComputerUseContext,
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
    # Deterministic fast path (2026-06-10 latency plan Task 4): a pure
    # "open <app>" goal is proven by the foreground window title the
    # observation already carries — no vision-LLM judge call (~1.5 s saved
    # per mission, and no done-reject loop risk for the most common goal
    # class). The model has already claimed done at this point; the title is
    # corroboration. A non-matching title falls through to the judge — this
    # branch can only ever SKIP cost, never reject.
    app_token = _open_goal_app_token(user_goal)
    if app_token:
        wt = str(getattr(observation, "window_title", "") or "").lower()
        if wt and app_token in wt:
            log.info(
                "[cu] done verified deterministically: %r in foreground "
                "title %r — skipping the LLM judge", app_token, wt[:60],
            )
            return (True, f"foreground window title proves '{app_token}' is open")

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
                timeout=_think_timeout_s(ctx),
            )
        except (TimeoutError, Exception) as exc:  # noqa: BLE001
            log.debug("[cu] compute-verifier failed (non-fatal): %s", exc)
            return (False, "")
        return _parse_verdict(raw)

    # Non-media goals (open/navigate/click/...) get the generic SINGLE-frame
    # judge -- no motion gap needed; the proof is a static screen state
    # (an open window, a visible page). Media/submit goals fall through to
    # the two-frame motion verifier below.
    if not _VERIFY_GOAL_RE.search(user_goal or ""):
        try:
            raw = await asyncio.wait_for(
                _call_brain(
                    ctx,
                    observation=observation,
                    user_goal=user_goal,
                    history_text="",
                    system_prompt=_GENERIC_VERIFIER_SYSTEM_PROMPT,
                    user_message=(
                        f"GOAL: {user_goal}\n\n"
                        "Judge the screenshot per the rules. JSON object only."
                    ),
                ),
                timeout=_think_timeout_s(ctx),
            )
        except (TimeoutError, Exception) as exc:  # noqa: BLE001
            log.debug("[cu] generic verifier failed (non-fatal): %s", exc)
            return (False, "")
        return _parse_verdict(raw)

    # Frame B: a fresh capture after a short gap so a real timer can tick.
    try:
        await asyncio.sleep(_VERIFY_FRAME_GAP_S)
        frame_b = await asyncio.wait_for(
            _observe(ctx, None), timeout=_OBSERVE_TIMEOUT_S + 0.5,
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
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
            timeout=_think_timeout_s(ctx),
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] verifier call failed/timed out (non-fatal): %s", exc)
        return (False, "")
    return _parse_verdict(raw)


def _parse_verdict(
    raw: str, *, bool_key: str = "done", text_key: str = "proof",
) -> tuple[bool, str]:
    """Parse a strict-judge JSON {<bool_key>:bool,<text_key>:str} (fence-tolerant).

    Defaults parse the completion-judge shape {"done":bool,"proof":str}; the
    fail-gate reuses it with bool_key="give_up", text_key="reason". Returns
    (False, "") on any malformed input -- verification never blocks (and a
    fail-judge that returns False means KEEP WORKING, never a free quit)."""
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
    flag = bool(verdict.get(bool_key) is True)
    text = str(verdict.get(text_key, ""))[:160]
    return (flag, text)


async def _verify_fail_justified(
    ctx: ComputerUseContext,
    *,
    observation: Observation,
    user_goal: str,
    claimed_reason: str,
) -> tuple[bool, str]:
    """Strict feasibility judge for the ``fail`` action — the symmetric sibling
    of :func:`_verify_goal_done`. Single-frame: does the screenshot PROVE the
    goal is impossible/blocked from here?

    Returns ``(give_up, reason)``. NEVER raises: on ANY error/timeout returns
    ``(False, "")`` -- i.e. KEEP WORKING. This is the core anti-reward-hack
    property: a broken/timed-out judge can never become a free quit. The bounded
    ``_MAX_FAIL_REJECTS`` backstop in the loop guarantees termination instead."""
    try:
        raw = await asyncio.wait_for(
            _call_brain(
                ctx,
                observation=observation,
                user_goal=user_goal,
                history_text="",
                system_prompt=_FAIL_VERIFIER_SYSTEM_PROMPT,
                user_message=(
                    f"GOAL: {user_goal}\n\n"
                    f"The agent wants to give up, claiming: {claimed_reason!r}\n"
                    "Judge feasibility from the screenshot per the rules. "
                    "Reply with the JSON object only."
                ),
            ),
            timeout=_think_timeout_s(ctx),
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
        log.debug("[cu] fail-verifier failed (non-fatal, keep working): %s", exc)
        return (False, "")
    return _parse_verdict(raw, bool_key="give_up", text_key="reason")


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
    "You are a capable agent: reason about a normally-phrased, everyday goal "
    "the way a person would -- you do NOT need the user to spell out every "
    "click. Infer the obvious intermediate steps yourself.\n"
    "Rules:\n"
    "* Plan as many steps as the goal naturally needs -- usually a handful. "
    "Each step is ONE concrete UI action: open / click / type / select / "
    "press. Prefer the shortest sequence that genuinely reaches the goal; do "
    "not pad with busywork, and do not cram several actions into one step.\n"
    "* ADAPT as you go: this plan is GUIDANCE, not a rigid macro. The executor "
    "re-observes the live screen after each action and may take an "
    "unplanned/reactive step (or re-plan) when the screen differs from your "
    "expectation -- so plan the sensible happy path and let the executor adapt "
    "to what actually appears.\n"
    "* PREFER THE INSTALLED DESKTOP APP. When the goal names an application "
    "that exists as a native desktop app (Discord, Slack, Spotify, Telegram, "
    "WhatsApp, Steam, a code editor, ...), the FIRST step opens that DESKTOP "
    "app via the open_app action (intent like 'open the discord desktop app') "
    "-- do NOT open the app inside a web browser, and do NOT navigate a browser "
    "to its website, unless the desktop app is genuinely unavailable (e.g. the "
    "goal explicitly says 'in the browser' / 'the web version', or after a "
    "launch attempt the desktop app clearly is not installed). The desktop app "
    "is faster, already signed in, and the path the user means.\n"
    "* LITERAL DICTATION: when the goal dictates specific words to type, say, "
    "write, or enter (e.g. 'type hello hello hello', 'say X'), the typing step's "
    "intent MUST carry those exact words verbatim -- never wrap them in a shell "
    "command or prepend 'echo'. This is distinct from a search topic: only "
    "'search for <topic>' yields a typed query.\n"
    "* Decompose multi-action goals into their real intermediate steps. E.g. "
    "'play a song' is: open the app -> click the search box -> type the song "
    "name -> press Enter -> click the matching track row -> press play -- not a "
    "single 'click play'. Use your own reasoning to find the analogous steps "
    "for the goal in front of you.\n"
    "* 'success' must be something a person could SEE on screen (text in a box, "
    "a result row, a now-playing title, an advancing timer) -- never an "
    "assumption.\n"
    "* The final step's success for media playback is: the elapsed-time counter "
    "is ADVANCING (the song is audibly playing), not merely a pause glyph.\n"
    "* NAVIGATION vs SEARCH (do NOT turn the goal's own words into a site "
    "search): a goal to FIND, READ, SHOW, OPEN, or LOOK AT a specific person's "
    "or account's posts, profile, news, latest, tweets, or page on a website "
    "is accomplished by NAVIGATING to that page (e.g. typing the account's "
    "profile address into the address bar) and STOPPING there. DO NOT type a "
    "descriptor word lifted from the goal -- 'news', 'latest', 'post', "
    "'tweet', 'update', 'profile' -- into the site's SEARCH BOX as a literal "
    "query: that lands on a generic search-results page, NOT the content the "
    "user asked for (recorded failure: 'show Elon Musk's news post on X' was "
    "mis-decomposed into a search for the literal word 'news', which derailed "
    "an otherwise-finished task). Only plan a typed search when the goal names "
    "an explicit TOPIC to search FOR (e.g. 'search YouTube for lo-fi beats' -> "
    "type 'lo-fi beats'); a person's name or account is a NAVIGATION target, "
    "not a search topic. For a 'see <account>'s posts/news on <site>' goal the "
    "final step's success is that account's page being visible -- never a "
    "search box containing one of the goal's words.\n"
)


async def _make_plan(
    ctx: ComputerUseContext,
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
            timeout=_think_timeout_s(ctx),
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
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


# Cached UI tree source for the per-step label enumeration (2026-06-09
# latency fix): constructing a fresh source every step paid setup cost for
# the same foreground enumeration. Built lazily on first use.
# Note for tests: reset to None via monkeypatch before exercising
# _get_ui_tree_source() directly (see test_cu_loop_robustness.py).
_UI_TREE_SOURCE: Any = None


def _get_ui_tree_source() -> Any:
    """Build the per-OS UI tree source once and reuse it across steps."""
    global _UI_TREE_SOURCE
    if _UI_TREE_SOURCE is None:
        from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

        _UI_TREE_SOURCE = make_ui_tree_source()
    return _UI_TREE_SOURCE


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
        obs = await asyncio.wait_for(_get_ui_tree_source().observe(), timeout=timeout_s)
    except (TimeoutError, Exception) as exc:  # noqa: BLE001
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


# ---------------------------------------------------------------------------
# Pixel-click zoom refinement + post-click verification (2026-06-10).
#
# Root cause of the chronic "agent misses its click targets" bug: on
# label-less surfaces (Spotify transport bar, custom-painted UIs) the loop
# executed the vision model's SINGLE coarse 0-1000 estimate directly — and
# vision LLMs cannot reliably ground a small control on a full-screen frame
# (live evidence 2026-05-27: six straight misses on the Calc "7" button).
# click_element fixed that for labeled controls; this fixes the pixel path:
#
#   1. REFINE — crop a zoomed region of the LIVE screen around the coarse
#      estimate, ask the brain to re-locate the target INSIDE the crop
#      (0-1000 normalized within the crop), map back to absolute pixels.
#   2. VERIFY — compare a small region around the clicked point before and
#      after the click. Unchanged pixels = the click hit dead space -> one
#      more refine round on a fresh crop, retry at the corrected position.
#
# Toggle safety (BUG-CU-TOGGLE family): a retry NEVER re-clicks within
# _REFINE_TOL_PX of an already-clicked point — a click that DID land (but
# whose effect shows elsewhere, e.g. skip resets the track title across the
# bar) must not be repeated, or it would skip a second song.
#
# Hermetic gates: the whole pass needs a real observation frame
# (screenshot_path) AND known monitor geometry; fakes/headless contexts keep
# the legacy single-click path. Every failure degrades to the coarse click.
# ---------------------------------------------------------------------------

#: Max click attempts per action: 1 initial + 2 corrected retries.
_CLICK_MAX_ATTEMPTS = 3
#: Refined point within this many pixels of an already-clicked point ->
#: do not click again (the first click is assumed to have landed).
_REFINE_TOL_PX = 12
#: Settle time between click and the post-click verification grab — long
#: enough for the UI to react, short enough to not drag the act phase.
_CLICK_VERIFY_SETTLE_S = 0.6
#: Verification crop half-side around the clicked point (mirrors the AI
#: Pointer's DEFAULT_CROP_RADIUS — big enough to include the reaction of a
#: transport bar, tight enough to ignore unrelated screen regions).
_VERIFY_CROP_RADIUS_PX = 110
#: Refine crop half-side as a fraction of the monitor width, floored. ~3.5x
#: zoom on a 2560px monitor — covers a coarse estimate that is off by up to
#: ~14% of the screen while keeping the target readable.
_REFINE_CROP_FRAC = 0.14
_REFINE_CROP_MIN_RADIUS_PX = 180

_REFINE_SYSTEM_PROMPT = (
    "You are a precision click-refinement assistant for desktop automation. "
    "You are given a ZOOMED-IN crop of the live screen, centered on a coarse "
    "click estimate. Locate the element described by TARGET inside the crop. "
    "Output exactly ONE JSON object, no prose, no code fences:\n"
    "  {\"found\": true, \"x\": <0-1000>, \"y\": <0-1000>} -- x/y on a "
    "0-1000 grid WITHIN THIS CROP (0,0 = crop top-left, 1000,1000 = crop "
    "bottom-right), aimed at the CENTER of the target element.\n"
    "  {\"found\": false} -- the target is NOT visible anywhere in the crop.\n"
    "Never guess a position for an element you cannot actually see."
)


def _refine_crop_bbox(
    x: int, y: int, monitor_geom: tuple[int, int, int, int],
) -> dict[str, int]:
    """Zoom-crop bbox around ``(x, y)``, clamped to the captured monitor."""
    from jarvis.vision.screenshot import region_bbox_around  # noqa: PLC0415

    left, top, width, height = monitor_geom
    radius = max(_REFINE_CROP_MIN_RADIUS_PX, round(width * _REFINE_CROP_FRAC))
    return region_bbox_around(x, y, radius, virtual_bounds=(left, top, width, height))


def _grab_region_jpeg(bbox: dict[str, int]) -> bytes | None:
    """Capture one live screen region as JPEG bytes; ``None`` on any failure
    (headless, mss missing, transient GDI error) so refinement/verification
    silently degrade to the plain click."""
    try:
        from jarvis.vision.screenshot import capture_region  # noqa: PLC0415

        return capture_region(bbox)
    except Exception:  # noqa: BLE001
        log.debug("[cu] region grab failed (non-fatal)", exc_info=True)
        return None


def _crop_norm_to_abs(bbox: dict[str, int], nx: int, ny: int) -> tuple[int, int]:
    """Map crop-relative 0-1000 coordinates to absolute screen pixels,
    clamped so a refined click can never leave the refined region."""
    nx = min(max(int(nx), 0), _COORD_NORM_MAX)
    ny = min(max(int(ny), 0), _COORD_NORM_MAX)
    ax = bbox["left"] + round(nx / _COORD_NORM_MAX * bbox["width"])
    ay = bbox["top"] + round(ny / _COORD_NORM_MAX * bbox["height"])
    ax = min(max(ax, bbox["left"]), bbox["left"] + bbox["width"] - 1)
    ay = min(max(ay, bbox["top"]), bbox["top"] + bbox["height"] - 1)
    return ax, ay


def _parse_refine_verdict(raw: str) -> tuple[bool, int, int] | None:
    """Parse {"found": bool, "x": n, "y": n} (fence-tolerant).

    Returns ``(True, nx, ny)`` with clamped crop-norm coordinates,
    ``(False, 0, 0)`` for an explicit not-visible verdict, or ``None`` on any
    malformed input — the caller then keeps the coarse estimate."""
    cleaned = (raw or "").strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    found = obj.get("found")
    if found is False:
        return (False, 0, 0)
    if found is not True:
        return None
    x, y = obj.get("x"), obj.get("y")
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(y, bool) or not isinstance(y, (int, float)):
        return None
    nx = min(max(int(x), 0), _COORD_NORM_MAX)
    ny = min(max(int(y), 0), _COORD_NORM_MAX)
    return (True, nx, ny)


async def _refine_click_point(
    ctx: ComputerUseContext,
    observation: Observation,
    x: int,
    y: int,
    monitor_geom: tuple[int, int, int, int],
    *,
    user_goal: str,
    target: str,
    retry_note: str = "",
) -> tuple[bool, int, int] | None:
    """One zoom-refinement round: live crop around ``(x, y)`` -> brain ->
    corrected absolute pixel. Returns ``(True, ax, ay)``, ``(False, 0, 0)``
    (target not in crop) or ``None`` on any failure (keep coarse estimate)."""
    bbox = _refine_crop_bbox(x, y, monitor_geom)
    jpeg = await asyncio.to_thread(_grab_region_jpeg, bbox)
    if not jpeg:
        return None
    block = ImageBlock(
        mime="image/jpeg", data_b64=base64.b64encode(jpeg).decode("ascii"),
    )
    user_message = (
        f"TARGET: {target or '(the element the GOAL needs clicked next)'}\n"
        f"GOAL: {user_goal}\n"
        f"The attached image is a zoomed-in crop of the live screen, "
        f"{bbox['width']}x{bbox['height']} screen pixels, centered on the "
        "current click estimate."
        + (f"\nNOTE: {retry_note}" if retry_note else "")
        + "\nReply with the JSON object only."
    )
    try:
        raw = await asyncio.wait_for(
            _call_brain(
                ctx,
                observation=observation,
                user_goal=user_goal,
                history_text="",
                system_prompt=_REFINE_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=64,
                images_override=[block],
            ),
            timeout=_think_timeout_s(ctx),
        )
    except (TimeoutError, Exception) as exc:  # noqa: BLE001 — degrade to coarse
        log.debug("[cu] click refine call failed (non-fatal): %s", exc)
        return None
    verdict = _parse_refine_verdict(raw)
    if verdict is None:
        return None
    found, nx, ny = verdict
    if not found:
        return (False, 0, 0)
    ax, ay = _crop_norm_to_abs(bbox, nx, ny)
    if (ax, ay) != (x, y):
        log.info(
            "[cu] click refine: (%d,%d) -> (%d,%d) [crop %dx%d at %d,%d]",
            x, y, ax, ay, bbox["width"], bbox["height"], bbox["left"], bbox["top"],
        )
    return (True, ax, ay)


async def _dispatch_raw_click(
    executor: Any, tool: Any, x: int, y: int, trace_id: Any,
) -> tuple[bool, str]:
    """The plain click-tool dispatch (extracted unchanged from the old click
    branch). TimeoutError propagates — the outer loop turns it into the
    mission timeout exactly as before."""
    args = {"x": x, "y": y, "button": "left", "double": False}
    try:
        res = await asyncio.wait_for(
            executor.execute(
                tool, args, user_utterance="computer-use", trace_id=trace_id,
            ),
            timeout=_ACT_TIMEOUT_S,
        )
    except TimeoutError:
        raise
    except Exception as exc:  # noqa: BLE001
        return False, f"click crash: {type(exc).__name__}: {exc}"
    return (
        bool(getattr(res, "success", False)),
        str(getattr(res, "output", "") or getattr(res, "error", "") or ""),
    )


async def _click_with_refine(
    obj: dict[str, Any],
    ctx: ComputerUseContext,
    *,
    executor: Any,
    tool: Any,
    trace_id: Any,
    user_goal: str,
    monitor_geom: tuple[int, int, int, int],
    observation: Observation | None,
) -> tuple[bool, str]:
    """Refine -> click -> verify -> (maybe) corrected retry for one pixel click."""
    abs_x, abs_y = _resolve_click_pixel(obj, monitor_geom)
    left, top, width, height = monitor_geom
    target = str(obj.get("target") or "").strip()
    refine_enabled = (
        width > 0
        and height > 0
        and observation is not None
        and bool(getattr(observation, "screenshot_path", None))
    )
    if not refine_enabled:
        return await _dispatch_raw_click(executor, tool, abs_x, abs_y, trace_id)
    verify_enabled = bool(getattr(ctx, "verify_after_each_step", True))

    x, y = abs_x, abs_y
    clicked: list[tuple[int, int]] = []
    last_msg = ""
    retry_note = ""
    for _attempt in range(_CLICK_MAX_ATTEMPTS):
        refined = None
        if clicked:
            # Trust-first (2026-06-10 latency plan Task 3): the refine pass is
            # a full LLM round-trip, and on the FIRST attempt it corrected the
            # model's point by <=5 px in live runs — pure cost (the executor
            # and the refiner see the same frame). Reserve it for retries
            # after a verified miss, where the zoomed live crop genuinely
            # re-locates the target.
            refined = await _refine_click_point(
                ctx, observation, x, y, monitor_geom,
                user_goal=user_goal, target=target, retry_note=retry_note,
            )
        if refined is not None:
            found, rx, ry = refined
            if not found:
                if clicked:
                    # Already clicked once and the target is no longer in the
                    # crop — likely the click DID work and the UI moved on.
                    return True, (
                        last_msg + " (target no longer in the refine crop — "
                        "verify via the next screenshot)"
                    )
                if target:
                    # The coarse estimate was so far off that the named
                    # target is not even in the zoom crop. Clicking blindly
                    # risks hitting the wrong control — re-plan instead.
                    return False, (
                        f"refine: target {target!r} not found near "
                        f"({x},{y}) — re-plan from a fresh screenshot"
                    )
                # No description to search for -> keep the coarse estimate.
            else:
                x, y = rx, ry
        if any(
            abs(px - x) <= _REFINE_TOL_PX and abs(py - y) <= _REFINE_TOL_PX
            for px, py in clicked
        ):
            # Toggle safety: the corrected point is the point we already
            # clicked. Re-clicking would double-fire (skip two songs, undo a
            # toggle) — accept click #1 and let the semantic layer judge.
            return True, (
                last_msg + " (refined point unchanged — not re-clicking; "
                "verify via the next screenshot)"
            )
        pre: bytes | None = None
        verify_bbox: dict[str, int] | None = None
        if verify_enabled:
            from jarvis.vision.screenshot import region_bbox_around  # noqa: PLC0415

            verify_bbox = region_bbox_around(
                x, y, _VERIFY_CROP_RADIUS_PX,
                virtual_bounds=(left, top, width, height),
            )
            pre = await asyncio.to_thread(_grab_region_jpeg, verify_bbox)
        ok, last_msg = await _dispatch_raw_click(executor, tool, x, y, trace_id)
        if not ok:
            return ok, last_msg
        clicked.append((x, y))
        if not verify_enabled or pre is None or verify_bbox is None:
            return ok, last_msg
        await asyncio.sleep(_CLICK_VERIFY_SETTLE_S)
        post = await asyncio.to_thread(_grab_region_jpeg, verify_bbox)
        if post is None or post != pre:
            # Something near the click visibly reacted (or we cannot tell) —
            # accept; the loop's fresh screenshot judges the semantics.
            return ok, last_msg
        log.info(
            "[cu] click at (%d,%d) produced no local change — refining for a "
            "corrected retry (%d/%d)", x, y, len(clicked), _CLICK_MAX_ATTEMPTS,
        )
        retry_note = (
            "A click at the crop center just produced NO visible change — it "
            "likely missed the element. Find the target's true position in "
            "this fresh crop."
        )
    return True, (
        last_msg + f" (no visible reaction near the target after "
        f"{len(clicked)} click(s) — verify and re-plan if needed)"
    )


async def _execute_action(
    obj: dict[str, Any],
    ctx: ComputerUseContext,
    *,
    trace_id: Any,
    user_goal: str,
    monitor_geom: tuple[int, int, int, int] = (0, 0, 0, 0),
    observation: Observation | None = None,
) -> tuple[bool, str]:
    """Run one parsed action through the tool layer.

    Returns ``(success, message)``. Terminal actions ``"done"`` and ``"fail"``
    are intercepted in :func:`run_cu_loop` BEFORE this is called; the
    defensive bottom branch only fires on a misrouted caller.

    ``monitor_geom`` is (left, top, width, height) of the monitor the
    screenshot was captured from. Click coordinates are translated from
    Gemini's 0-1000 normalized grid to an absolute screen pixel via
    :func:`_resolve_click_pixel` (BUG-CU-MULTIMON + BUG-CU-NORMCOORD).

    ``observation`` (the step's screenshot observation) arms the pixel-click
    zoom-refinement + verification pass; without it (direct callers, fakes,
    headless) the legacy single-click path runs unchanged.
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
        return await _click_with_refine(
            obj, ctx,
            executor=executor, tool=tool, trace_id=trace_id,
            user_goal=user_goal, monitor_geom=monitor_geom,
            observation=observation,
        )

    if action == "type":
        tool = tools.get("type_text")
        if tool is None:
            return False, "type_text tool not wired"
        # Let a freshly-focused input settle before typing (anti leading-char
        # drop on webview/Tauri terminals; CU typo bug 2026-06-15).
        await asyncio.sleep(_PRE_TYPE_SETTLE_S)
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"text": str(obj.get("text", ""))},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_ACT_TIMEOUT_S,
            )
        except TimeoutError:
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
                timeout=_ACT_TIMEOUT_S,
            )
        except TimeoutError:
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
                timeout=_ACT_TIMEOUT_S,
            )
        except TimeoutError:
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
                timeout=_ACT_TIMEOUT_S,
            )
        except TimeoutError:
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
                timeout=_ACT_TIMEOUT_S,
            )
        except TimeoutError:
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
    ctx: ComputerUseContext,
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
    ctx: ComputerUseContext,
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
    # LLM-failure retry budget (2026-06-09 shippability fix): a single
    # malformed model response, provider hiccup, or slow brain call used to
    # END the whole mission instantly (exit 2/124) — the #1 "task aborted in
    # the middle" source. Now each such failure injects a correction note
    # into the history and retries from a fresh screenshot; only this many
    # failures per mission end it, with a clean error message.
    _MAX_LLM_FAILURES = 3
    llm_failures = 0
    # Done-verification gate (2026-06-09 shippability fix): a model-emitted
    # "done" is only accepted once the strict judge confirms the goal on the
    # CURRENT screenshot (``ctx.verify_after_each_step`` -- previously a dead
    # config knob). A rejected done feeds the judge's proof back into the
    # history so the model self-corrects; after this many rejects the mission
    # ends with an explicit "not verifiably achieved" failure instead of
    # looping forever or silently claiming success.
    _MAX_DONE_REJECTS = 3
    done_rejects = 0
    fail_rejects = 0  # symmetric fail-gate budget (module _MAX_FAIL_REJECTS)
    verify_done_enabled = bool(getattr(ctx, "verify_after_each_step", True))
    # The last successful state-changing action -- fed into the next executor
    # turn as a VERIFY FIRST directive so the model checks the fresh
    # screenshot for the action's effect before acting again.
    last_state_change = ""
    # Count of successful state-changing actions this mission; drives the
    # plan's >>> current-step marker (pure waits never advance the plan).
    completed_state_changes = 0
    # Anti-oscillation + no-reopen guards (BUG-CU-TOGGLE, 2026-05-28). The
    # no-progress hash guard above only catches an UNCHANGED screen; a
    # play/pause toggle FLIPS the icon every click (screen changes), so it
    # slips past. These mission-scoped guards stop the thrash:
    #   * recent_click_targets: the last few NORMALIZED (0-1000) click points.
    #     A repeat of the SAME point (within _CLICK_SAME_TOL) _CLICK_REPEAT_LIMIT
    #     times is a toggle-thrash -- we do NOT execute it (parity-safe: leaves
    #     the system in the state produced by click #1) and force one
    #     verification re-plan instead. The tolerance is TIGHT on purpose: a
    #     toggle re-hits the SAME control, whereas navigating a vertically
    #     stacked dropdown clicks DIFFERENT rows that share an x and sit only a
    #     row apart in y. A coarse tolerance conflated the two and suppressed a
    #     brand-new row as a "thrash" (BUG-CU-DROPDOWN-THRASH, live 2026-06-17:
    #     the Energieoptionen mission froze on row 365 because it fell within 25
    #     of two earlier, DIFFERENT rows -> "3 identical screenshots" abort).
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
    # Tight: only a near-IDENTICAL re-click counts as a toggle. Adjacent
    # dropdown rows sit >=~13 normalized units apart, so 8 keeps list
    # navigation (distinct rows) from being mistaken for a same-point thrash
    # while still absorbing the small jitter of a model re-aiming one control.
    _CLICK_SAME_TOL = 8
    _CLICK_REPEAT_LIMIT = 2
    _MAX_LAUNCHES_PER_APP = 1
    recent_click_targets: _deque[tuple[int, int]] = _deque(maxlen=6)
    opened_apps: dict[str, int] = {}
    toggle_stop_engaged = False
    # Cumulative guard-blocked actions this mission (see _MAX_GUARD_HITS).
    guard_hits = 0
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

    # Per-mission phase wall-time accumulator (latency plan Task 7) — turned
    # into the one-line "[cu] mission profile" summary on every _final, so a
    # single log line answers "where did the time go".
    phase_ms: dict[str, float] = {}
    step_idx = 0

    def _final(stdout: str = "", stderr: str = "", exit_code: int = 0) -> HarnessResult:
        total_s = (time.time_ns() - t_start) / 1e9
        parts = " ".join(
            f"{k}={v / 1000.0:.1f}s" for k, v in sorted(phase_ms.items())
        )
        profile = (
            f"[cu] mission profile: steps={step_idx} "
            f"total={total_s:.1f}s {parts}".rstrip() + "\n"
        )
        log.info(profile.rstrip())
        return HarnessResult(
            stdout=stdout,
            stderr=stderr + profile,
            exit_code=exit_code,
            duration_ms=(time.time_ns() - t_start) // 1_000_000,
            is_final=True,
        )

    def _progress(msg: str) -> HarnessResult:
        return HarnessResult(stdout=msg + "\n", is_final=False)

    yield _progress(f"[cu] Start: {task_prompt[:80]}")

    # Deterministic screenshot fast path (over-execution fix, 2026-06-16): a
    # bare "take a screenshot" goal is a ONE-SHOT capture, not a GUI task. We
    # satisfy it directly — capture + save in a SINGLE step — instead of asking
    # a weak fast-tier model to operate the Snipping Tool (the live 17-step /
    # 38 s flail that re-clicked "Neuer Screenshot" three times, discarding each
    # capture, which the user saw as "deleted three screenshots"). On ANY
    # capture failure (headless VPS / missing desktop extras) the helper returns
    # None and we fall through to the interactive loop unchanged — no regression.
    if _is_pure_screenshot_goal(task_prompt):
        saved = _save_user_screenshot()
        if saved is not None:
            log.info("[cu] screenshot fast path: saved %s (no GUI loop)", saved)
            yield _final(
                stdout=f"[cu] screenshot saved: {saved}\n",
                exit_code=0,
            )
            return
        log.info(
            "[cu] screenshot fast path unavailable (capture returned None) — "
            "falling through to the interactive loop",
        )

    # Loop-internal mission deadline (Wave 0): end cleanly with an explicit
    # budget result BEFORE the harness wait_for guillotines the stream
    # mid-step — the user then hears an honest completion announcement
    # instead of a hard timeout.
    mission_deadline = time.monotonic() + _internal_deadline_s(
        float(getattr(task, "timeout_s", 120) or 120),
    )
    # Spoken progress milestones (kind="progress"), throttled.
    last_progress_announce_ts = 0.0
    announced_steps = 0

    for step_idx in range(1, max_steps + 1):
        if _is_cancelled(cancel_token):
            yield _final(stderr="[cu] cancelled\n", exit_code=_CANCEL_EXIT_CODE)
            return
        if time.monotonic() >= mission_deadline:
            yield _final(
                stderr=(
                    f"[cu] mission budget exhausted at step {step_idx} — "
                    "ending cleanly before the harness deadline\n"
                ),
                exit_code=_BUDGET_EXIT_CODE,
            )
            return

        # Observe. Heartbeat first so a stall here is attributable in the log
        # (BUG-CU-STALL: a silent 28s do-nothing was impossible to localize).
        # The UIA label enumeration is independent I/O and runs CONCURRENTLY
        # with the screenshot (2026-06-09 latency fix): per step we pay
        # max(screenshot, uia) instead of screenshot + uia.
        log.info("[cu] step %d phase=observe+uia", step_idx)
        t_observe = time.monotonic()
        labels_task = asyncio.create_task(
            _foreground_clickable_labels(_UIA_TIMEOUT_S),
            name=f"cu-labels-step-{step_idx}",
        )
        try:
            observation = await _observe(ctx, cancel_token)
        except TimeoutError:
            labels_task.cancel()
            yield _final(
                stderr=f"[cu] observe timeout (step {step_idx})\n",
                exit_code=_TIMEOUT_EXIT_CODE,
            )
            return
        except Exception as exc:  # noqa: BLE001
            labels_task.cancel()
            yield _final(
                stderr=f"[cu] observe failed: {exc}\n",
                exit_code=_OBSERVE_EXIT_CODE,
            )
            return
        # Collect the enumeration result NOW so no task dangles on any of the
        # early-return paths below (_foreground_clickable_labels never raises).
        control_labels = await labels_task
        await _profile_phase(
            ctx, phase="observe", step_idx=step_idx, t0=t_observe, acc=phase_ms,
        )

        # No-progress guard: if the last _STUCK_LIMIT screenshots are
        # byte-identical, nothing on screen changed. Bail with a clear "stuck"
        # failure instead of grinding through the rest of the budget.
        # Cause attribution is data-driven (live 2026-06-17): if a guard had
        # been SUPPRESSING the model's actions, the freeze is because nothing
        # executed -- saying "off-screen" then misdiagnoses the abort. Only
        # when no action was guard-blocked is the dead-target reading correct.
        if observation.screenshot_hash:
            recent_hashes.append(observation.screenshot_hash)
            if (
                len(recent_hashes) == _STUCK_LIMIT
                and len(set(recent_hashes)) == 1
            ):
                cause = (
                    "recent actions were suppressed by a guard "
                    "(repeated click / relaunch), so nothing changed"
                    if guard_hits > 0
                    else "the click target is unreactive or off-screen"
                )
                yield _final(
                    stderr=(
                        f"[cu] no progress: {_STUCK_LIMIT} identical "
                        f"screenshots in a row at step {step_idx} -- "
                        f"{cause}.\n"
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

        # UIA-first grounding (BUG-CU-GROUNDING): the clickable control names
        # were already enumerated concurrently with the screenshot above, so
        # the model can click_element by an EXACT name (deterministic) instead
        # of pixel-guessing a small button. Empty for label-less surfaces
        # (Spotify/games) -> the hint is omitted and the loop stays
        # pixel-first. TIGHT 3s budget (BUG-CU-STALL): a wedged UIA COM call
        # must never block the click path -- it returns [] and the loop
        # proceeds with pixel grounding rather than stalling.
        controls_hint = ""
        if control_labels:
            controls_hint = (
                "\n\nAVAILABLE CONTROLS (click_element by one of these EXACT "
                "names -- do NOT pixel-guess these): "
                + ", ".join(f'"{n}"' for n in control_labels)
            )

        # Plan-first: generate the ordered plan once, after the first
        # screenshot, for every MULTI-STEP goal -- music/search goals (the
        # original class) plus compound/navigation goals ("oeffne X und ...",
        # 2026-06-09 shippability fix). A compute goal ("rechne 8x8") or a
        # simple single-verb action still skips the planner round-trip
        # (BUG-CU-STALL). On planner failure plan stays [] -> stateless loop.
        if not plan_attempted and _goal_needs_plan(task_prompt):
            plan_attempted = True
            log.info("[cu] step %d phase=plan", step_idx)
            t_plan = time.monotonic()
            plan = await _make_plan(
                ctx, observation=observation, user_goal=task_prompt,
            )
            await _profile_phase(
                ctx, phase="plan", step_idx=step_idx, t0=t_plan, acc=phase_ms,
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
        # BUG-CU-EMPTYTITLE (2026-06-09): an EMPTY title is NOT proof of the
        # desktop — text-heavy apps (Chrome, VS Code, Slack, …) run in
        # screenshot mode where the source historically reported "" for every
        # frame, so the old `wt == ""` arm fired a false REGRESSION right
        # after open_app and told the model to re-open the app it was using.
        # Only the explicit shell titles count as "fell to the desktop".
        win_title = (getattr(observation, "window_title", "") or "")
        wt = win_title.strip().lower()
        if (expected_window_token and step_idx > 1
                and wt in ("program manager", "task switching")):
            log.info(
                "[cu] REGRESSION: foreground fell to the desktop (title=%r) — "
                "last action likely closed the app", win_title,
            )
            history.append(
                "REGRESSION: the app window is gone — the desktop is now in "
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
            # Advance the current step by counting successful STATE-CHANGING
            # actions (clicks/types/keys/launches). The old heuristic counted
            # " ok " substrings in the history, so pure waits and incidental
            # notes pushed the >>> marker ahead of reality (2026-06-09 fix).
            current_step = min(completed_state_changes, len(plan) - 1)
            cur = plan[current_step]
            plan_user_message = (
                f"GOAL: {task_prompt}\n\n"
                f"PLAN:\n{_render_plan(plan, current_step)}\n\n"
                f"CURRENT STEP: {cur['intent']}\n"
                f"SUCCESS WHEN: {cur.get('success') or 'the step is visibly done'}\n\n"
                f"RECENT_STEPS:\n{chr(10).join(history[-8:]) or '(none)'}\n\n"
                "Do ONLY the current step. Emit the JSON action(s) for it. "
                "Emit {\"action\":\"done\"} ONLY when the FINAL plan step's "
                "success is visibly proven in the screenshot."
            )
            if _goal_needs_search(task_prompt):
                # Music-goal-only block (BUG-CU-WRONG-SONG). Injecting it into
                # every planned turn confused navigation goals with Spotify
                # rules, so it is scoped to play/search goals (2026-06-09).
                plan_user_message += _SEARCH_DISCIPLINE_BLOCK
            plan_user_message += controls_hint
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
        # VERIFY FIRST directive (2026-06-09): after a state-changing action,
        # the next executor turn explicitly names that action and instructs
        # the model to check the fresh screenshot for its effect before
        # acting again -- the zero-extra-latency half of after-step
        # verification (the screenshot is taken anyway; no extra model call).
        if verify_done_enabled and last_state_change:
            verify_note = (
                f"\n\nVERIFY FIRST: your previous action was "
                f"[{last_state_change}]. Check the CURRENT screenshot: did it "
                "have the intended effect? If NOT, do not repeat it blindly -- "
                "try a DIFFERENT element or approach. If the whole goal is now "
                "visibly achieved, emit {\"action\": \"done\"}."
            )
            if plan_user_message is None:
                plan_user_message = (
                    f"GOAL: {task_prompt}\n"
                    f"PREVIOUS_STEPS:\n"
                    f"{chr(10).join(history[-12:]) or '(none)'}\n\n"
                    "Inspect the screenshot and emit ONE JSON action."
                    + verify_note
                )
                # A failed planner on a music goal lands here -- the
                # anti-shortcut discipline must survive that path too
                # (review finding 2026-06-09).
                if _goal_needs_search(task_prompt):
                    plan_user_message += _SEARCH_DISCIPLINE_BLOCK
            else:
                plan_user_message += verify_note
        log.info("[cu] step %d phase=think", step_idx)
        t_think = time.monotonic()
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
                    timeout=_think_timeout_s(ctx),
                )
            except TimeoutError:
                llm_failures += 1
                log.info(
                    "[cu] brain timeout (step %d, failure %d/%d)",
                    step_idx, llm_failures, _MAX_LLM_FAILURES,
                )
                if llm_failures >= _MAX_LLM_FAILURES:
                    yield _final(
                        stderr=(
                            f"[cu] giving up after {llm_failures} model "
                            f"failures (last: brain timeout at step "
                            f"{step_idx})\n"
                        ),
                        exit_code=_TIMEOUT_EXIT_CODE,
                    )
                    return
                await _profile_phase(
                    ctx, phase="think", step_idx=step_idx, t0=t_think,
                    acc=phase_ms,
                )
                yield _progress(
                    f"[cu] step {step_idx}: brain timeout -- retrying"
                )
                continue
            except Exception as exc:  # noqa: BLE001
                llm_failures += 1
                log.info(
                    "[cu] brain failed (step %d, failure %d/%d): %s",
                    step_idx, llm_failures, _MAX_LLM_FAILURES, exc,
                )
                if llm_failures >= _MAX_LLM_FAILURES:
                    yield _final(
                        stderr=(
                            f"[cu] giving up after {llm_failures} model "
                            f"failures (last: {exc})\n"
                        ),
                        exit_code=_PARSE_EXIT_CODE,
                    )
                    return
                await _profile_phase(
                    ctx, phase="think", step_idx=step_idx, t0=t_think,
                    acc=phase_ms,
                )
                yield _progress(
                    f"[cu] step {step_idx}: brain failed -- retrying"
                )
                continue

            # Parse — model may return a single action object OR a list of
            # action objects (a batch). Both shapes are validated and normalised
            # to a list, so the executor below iterates uniformly.
            try:
                batch = _parse_actions(raw)
            except CULoopError as exc:
                llm_failures += 1
                log.info(
                    "[cu] parse failed (step %d, failure %d/%d): %s",
                    step_idx, llm_failures, _MAX_LLM_FAILURES, exc,
                )
                if llm_failures >= _MAX_LLM_FAILURES:
                    yield _final(
                        stderr=(
                            f"[cu] giving up after {llm_failures} model "
                            f"failures (last parse error: {exc})\n"
                        ),
                        exit_code=_PARSE_EXIT_CODE,
                    )
                    return
                # Teach the model what went wrong so the retry self-corrects.
                history.append(
                    f"YOUR LAST RESPONSE WAS INVALID ({str(exc)[:80]}). "
                    "Respond with the JSON action object(s) ONLY -- no prose, "
                    "no code fences."
                )
                await _profile_phase(
                    ctx, phase="think", step_idx=step_idx, t0=t_think,
                    acc=phase_ms,
                )
                yield _progress(
                    f"[cu] step {step_idx}: invalid model response -- retrying"
                )
                continue

        await _profile_phase(
            ctx, phase="think", step_idx=step_idx, t0=t_think, acc=phase_ms,
        )
        if len(batch) > 1:
            log.info(
                "[cu] step %d batch size = %d (plan-then-execute)",
                step_idx, len(batch),
            )

        # Batch executor — runs the whole list under ONE screenshot. A
        # ``done`` or ``fail`` ends the mission immediately; any other
        # action failure breaks the batch and falls back to the outer
        # loop for a fresh screenshot + re-plan.
        # Tracks whether THIS batch already changed the screen state — the
        # done-judge must then re-observe instead of judging the stale
        # pre-batch screenshot (review finding 2026-06-09).
        batch_did_state_change = False
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
                    guard_hits += 1
                    if guard_hits >= _MAX_GUARD_HITS:
                        yield _final(
                            stderr=(
                                f"[cu] mission is circling: {guard_hits} "
                                "guard-blocked actions this mission (suppressed "
                                "relaunches / repeated clicks) — no productive "
                                "next action found\n"
                            ),
                            exit_code=_FAIL_EXIT_CODE,
                        )
                        return
                    continue
                if _app:
                    opened_apps[_app] = _launches + 1
                    # Remember the app so the regression detector can notice if a
                    # later misclick drops us to the desktop (BUG-CU-MISCLICK); on
                    # a genuine close it pops this entry, re-allowing ONE fresh
                    # launch so recover-after-close still works.
                    expected_window_token = _app

            # Repeated-click / toggle-thrash guard (BUG-CU-TOGGLE): re-clicking
            # the SAME point (within _CLICK_SAME_TOL) is a toggle thrash
            # (play/pause flips the icon every click, so the no-progress hash
            # guard never trips). Do NOT execute the repeat -- breaking BEFORE
            # _execute_action is parity-safe: the system stays in the state
            # produced by click #1 ("playing"). Force one verification re-plan.
            # The match is on near-IDENTICAL points only: counting every nearby
            # point conflated stepping through a stacked dropdown (distinct
            # rows) with a same-spot thrash (BUG-CU-DROPDOWN-THRASH).
            if action == "click":
                _tx = int(action_obj.get("x", -999))
                _ty = int(action_obj.get("y", -999))
                _near = sum(
                    1 for (px, py) in recent_click_targets
                    if abs(px - _tx) <= _CLICK_SAME_TOL
                    and abs(py - _ty) <= _CLICK_SAME_TOL
                )
                recent_click_targets.append((_tx, _ty))
                if _near >= _CLICK_REPEAT_LIMIT:
                    toggle_stop_engaged = True
                    log.info(
                        "[cu] %s repeated click ~(%d,%d) x%d — toggle-stop "
                        "(not executed)", tag, _tx, _ty, _near,
                    )
                    guard_hits += 1
                    if guard_hits >= _MAX_GUARD_HITS:
                        yield _final(
                            stderr=(
                                f"[cu] mission is circling: {guard_hits} "
                                "guard-blocked actions this mission (suppressed "
                                "relaunches / repeated clicks) — no productive "
                                "next action found\n"
                            ),
                            exit_code=_FAIL_EXIT_CODE,
                        )
                        return
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
                # Done-gate (2026-06-09): EVERY "done" is checked by the
                # strict judge while verify_after_each_step is on -- compute
                # goals against the calculator display (BUG-CU-RESULT), media
                # goals against frame motion, everything else against the
                # generic single-frame proof ("open Chrome" must show an open
                # Chrome window, not a typed search query). Disabled only via
                # config; compute goals stay verified regardless (their old
                # always-on behaviour).
                if verify_done_enabled or _goal_needs_result(task_prompt):
                    # If this batch already executed a state-changing action
                    # (e.g. [open_app, done]), the step screenshot predates
                    # that action -- re-observe so the judge sees the CURRENT
                    # screen, not the stale pre-batch frame (review 2026-06-09).
                    verify_obs = observation
                    if batch_did_state_change:
                        try:
                            verify_obs = await _observe(ctx, cancel_token)
                        except Exception:  # noqa: BLE001
                            log.debug(
                                "[cu] fresh verify observe failed; judging the "
                                "pre-batch frame", exc_info=True,
                            )
                    t_verify = time.monotonic()
                    ok, proof = await _verify_goal_done(
                        ctx, observation=verify_obs, user_goal=task_prompt,
                    )
                    await _profile_phase(
                        ctx, phase="verify", step_idx=step_idx, t0=t_verify,
                        acc=phase_ms,
                    )
                    if not ok:
                        done_rejects += 1
                        log.info(
                            "[cu] %s done REJECTED (%d/%d) — not verified (%s)",
                            tag, done_rejects, _MAX_DONE_REJECTS, proof[:80],
                        )
                        if done_rejects >= _MAX_DONE_REJECTS:
                            yield _final(
                                stderr=(
                                    f"[cu] goal not verifiably achieved after "
                                    f"{done_rejects} completion attempts "
                                    f"(last evidence: {proof[:100] or 'none'})\n"
                                ),
                                exit_code=_FAIL_EXIT_CODE,
                            )
                            return
                        if _goal_needs_result(task_prompt):
                            history.append(
                                f"RESULT NOT CONFIRMED ({proof[:120]}). The "
                                "calculator does not show the correct answer yet. "
                                "Clear it (press 'Escape' or click 'C') and re-enter "
                                "the calculation using click_element on the named "
                                "digit/operator keys, then press 'Gleich'."
                            )
                        else:
                            history.append(
                                f"DONE REJECTED: the screenshot does not prove "
                                f"the goal yet ({proof[:120] or 'no evidence'}). "
                                "Keep working: pick the next concrete action "
                                "that visibly advances the goal."
                            )
                        break  # re-plan from a fresh screenshot
                    log.info("[cu] %s done verified: %s", tag, proof[:80])
                    yield _final(
                        stdout=f"[cu] done at {tag} (verified: {proof[:80]})\n",
                        exit_code=0,
                    )
                    return
                yield _final(
                    stdout=f"[cu] done at {tag}\n", exit_code=0,
                )
                return
            if action == "fail":
                reason = (
                    str(action_obj.get("reason", "")).strip()
                    or "model declined"
                )
                # Fail-gate (2026-06-15): the SYMMETRIC sibling of the done-gate
                # above. While verification is on, a voluntary give-up is NOT
                # honored on the model's word -- the strict feasibility judge
                # must AGREE the goal is genuinely impossible/blocked from the
                # current screen. Otherwise the fail is rejected, the model is
                # told to keep working, and the loop re-plans from a fresh
                # screenshot (mirror of done_rejects). Bounded by
                # _MAX_FAIL_REJECTS so an honestly impossible task still
                # terminates. This closes the reward-hack where quitting was
                # free while succeeding was judge-gated (live 2026-06-15
                # Snipping-Tool turn: emitted fail with the overlay on screen).
                if verify_done_enabled:
                    verify_obs = observation
                    if batch_did_state_change:
                        try:
                            verify_obs = await _observe(ctx, cancel_token)
                        except Exception:  # noqa: BLE001
                            log.debug(
                                "[cu] fresh fail-verify observe failed; judging "
                                "the pre-batch frame", exc_info=True,
                            )
                    give_up, jreason = await _verify_fail_justified(
                        ctx, observation=verify_obs, user_goal=task_prompt,
                        claimed_reason=reason,
                    )
                    if not give_up:
                        fail_rejects += 1
                        log.info(
                            "[cu] %s fail REJECTED (%d/%d) — goal still looks "
                            "achievable (%s)",
                            tag, fail_rejects, _MAX_FAIL_REJECTS,
                            jreason[:80] or "no proof of impossibility",
                        )
                        if fail_rejects < _MAX_FAIL_REJECTS:
                            why = jreason[:120] or "no proof it is impossible"
                            history.append(
                                "FAIL REJECTED: the goal still looks achievable "
                                f"from here ({why}). Do NOT give up. Pick the "
                                "next concrete action that visibly advances the "
                                "goal."
                            )
                            break  # re-plan from a fresh screenshot
                        # Budget reached -> honor the give-up: verified-
                        # impossible after _MAX_FAIL_REJECTS attempts.
                        log.info(
                            "[cu] %s fail honored after %d rejects (backstop)",
                            tag, fail_rejects,
                        )
                    elif jreason:
                        reason = jreason  # surface the judge's VERIFIED reason
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
            t_act = time.monotonic()
            try:
                success, message = await _execute_action(
                    action_obj, ctx,
                    trace_id=observation.trace_id, user_goal=task_prompt,
                    monitor_geom=monitor_geom, observation=observation,
                )
            except TimeoutError:
                yield _final(
                    stderr=f"[cu] action timeout at {tag}\n",
                    exit_code=_TIMEOUT_EXIT_CODE,
                )
                return
            await _profile_phase(
                ctx, phase="act", step_idx=step_idx, t0=t_act, acc=phase_ms,
            )

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
            # Settle probe: a launched app needs 1-3 s to paint its window;
            # observing immediately wastes a full think round on the stale
            # pre-launch frame (latency plan Task 6).
            if action == "open_app":
                await _settle_after_open_app(
                    ctx, str(action_obj.get("name", "")).strip().lower(),
                )
            # Remember the last state-changing action for the next turn's
            # VERIFY FIRST directive (wait is a pure pause, never state).
            if action != "wait":
                last_state_change = f"{action} " + json.dumps(
                    {k: v for k, v in action_obj.items() if k != "action"},
                )[:80]
                completed_state_changes += 1
                batch_did_state_change = True
                # Spoken milestone (Wave 0): "Schritt N von M erledigt." —
                # deterministic, no LLM call (AP-11 spirit), throttled so a
                # fast batch never produces a barrage of speech. kind=
                # "progress" lets the pipeline drop stale ones.
                # OFF by default since 2026-06-10: completed_state_changes
                # counts ok-ACTIONS, not verified plan steps, so the spoken
                # counter inflated to "6 von 6 erledigt" on a mission that
                # then kept running and failed. Opt in via
                # [computer_use].announce_progress.
                if (plan and ctx.bus is not None
                        and getattr(ctx, "announce_progress", False)):
                    done_steps = min(completed_state_changes, len(plan))
                    _now = time.monotonic()
                    if (done_steps > announced_steps
                            and _now - last_progress_announce_ts
                            >= _PROGRESS_MIN_INTERVAL_S):
                        announced_steps = done_steps
                        last_progress_announce_ts = _now
                        # Detached on purpose: awaiting this publish blocks
                        # the loop for the whole TTS synthesis (BUG-CU-
                        # ANNOUNCE-BLOCK) — see _publish_announcement_nonblocking.
                        _publish_announcement_nonblocking(ctx.bus, AnnouncementRequested(
                            text=(
                                f"Schritt {done_steps} von {len(plan)} "  # i18n-allow
                                "erledigt."  # i18n-allow
                            ),
                            priority="normal",
                            language="de",
                            kind="progress",
                        ))
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
