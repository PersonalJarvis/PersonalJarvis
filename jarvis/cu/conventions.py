"""Provider coordinate conventions + action grammar for Computer-Use v2.

The legacy engine hardcoded Gemini's 0-1000 normalized grid into the prompt
AND the pixel resolver — every provider whose vision models emit pixel
coordinates on the sent image (the Claude / OpenAI computer-use convention)
was systematically mis-mapped. Here the convention is resolved per brain
(capability first, metadata default second) and both the prompt block and
the coordinate resolution are derived from that ONE decision.

Resolution order (AP-21 — capability over provider name):

1. Explicit config pin: ``[computer_use].coordinate_space`` (``auto`` = off).
2. A ``coordinate_convention`` capability attribute on the brain instance —
   the extension point for provider plugins to declare their space.
3. Metadata defaults by provider family (a DATA table, not code branches):
   the Gemini family documents the 0-1000 grid; Claude and OpenAI document
   image-pixel coordinates. Unknown providers default to ``normalized_1000``
   because any instruction-following model can comply with it and it is
   resolution-independent (the legacy engine live-proved it broadly).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from jarvis.cu.geometry import CoordinateConvention

logger = logging.getLogger(__name__)

_CONVENTIONS: frozenset[str] = frozenset({"normalized_1000", "image_pixels"})

#: Metadata defaults per provider-family token (matched as a substring of the
#: provider name, lowercased). Data, not logic — extend by adding a row.
_FAMILY_DEFAULTS: tuple[tuple[str, CoordinateConvention], ...] = (
    ("gemini", "normalized_1000"),
    ("google", "normalized_1000"),
    ("claude", "image_pixels"),
    ("anthropic", "image_pixels"),
    ("openai", "image_pixels"),
    ("azure", "image_pixels"),
)

_DEFAULT_CONVENTION: CoordinateConvention = "normalized_1000"


def resolve_convention(
    provider: str,
    brain: Any = None,
    *,
    config_override: str = "auto",
) -> CoordinateConvention:
    """Resolve which coordinate space this brain's output is parsed in."""
    override = (config_override or "auto").strip().lower()
    if override in _CONVENTIONS:
        return override  # type: ignore[return-value]

    declared = getattr(brain, "coordinate_convention", None)
    if isinstance(declared, str) and declared in _CONVENTIONS:
        return declared  # type: ignore[return-value]

    p = (provider or "").strip().lower()
    for token, convention in _FAMILY_DEFAULTS:
        if token in p:
            return convention
    return _DEFAULT_CONVENTION


def coordinate_prompt_block(
    convention: CoordinateConvention, image_width: int, image_height: int,
) -> str:
    """The COORDINATE SYSTEM section of the executor prompt, per convention."""
    if convention == "normalized_1000":
        return (
            "COORDINATE SYSTEM: x and y are on a 0-1000 NORMALIZED grid over "
            "the screenshot (0,0 = top-left, 1000,1000 = bottom-right), "
            "independent of the image's pixel size. Aim at the CENTER of the "
            "target element. NEVER return raw pixel values."
        )
    return (
        f"COORDINATE SYSTEM: x and y are PIXEL coordinates on the screenshot "
        f"as attached, which is {image_width}x{image_height} pixels "
        f"(0,0 = top-left). Aim at the CENTER of the target element. Never "
        f"return coordinates outside the image."
    )


# ---------------------------------------------------------------------------
# Action grammar
# ---------------------------------------------------------------------------

VALID_ACTIONS: frozenset[str] = frozenset({
    "click", "click_element", "type", "key", "scroll", "drag",
    "open_app", "switch_window", "wait", "done", "fail",
})

#: Hard caps mirrored from the legacy grammar (stable, proven values).
MAX_WAIT_MS = 10_000
MAX_BATCH = 5
DEFAULT_SCROLL_AMOUNT = 3
DEFAULT_DRAG_DURATION_MS = 400
_SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})
_BUTTONS = frozenset({"left", "right", "middle"})

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def action_grammar_block() -> str:
    """The ACTIONS section of the executor prompt (convention-independent)."""
    return (
        "Reply with ONE JSON action object, or a SHORT array (max "
        f"{MAX_BATCH}) of actions that belong together. No prose, no code "
        "fences around your final answer beyond a single JSON block.\n"
        "Actions:\n"
        '  {"action": "click", "x": <num>, "y": <num>, "target": "<what you '
        'are clicking>", "button": "left|right|middle", "double": false}\n'
        '  {"action": "click_element", "name": "<exact visible control '
        'label>"}   preferred when the label appears in CLICKABLE ELEMENTS\n'
        '  {"action": "type", "text": "<text>", "clear_first": false}   types '
        "into the FOCUSED field; set clear_first true to replace existing "
        "content (address bars, search boxes)\n"
        '  {"action": "key", "keys": ["enter"]}   key or combo, e.g. '
        '["ctrl","t"]\n'
        '  {"action": "scroll", "direction": "up|down|left|right", '
        '"amount": <notches>, "x": <num>, "y": <num>}   x/y optional\n'
        '  {"action": "drag", "x": <num>, "y": <num>, "x2": <num>, '
        '"y2": <num>, "duration_ms": 400}\n'
        '  {"action": "open_app", "name": "<app>"}\n'
        '  {"action": "switch_window", "name": "<window-title substring>"}\n'
        '  {"action": "wait", "ms": <0-10000>}\n'
        '  {"action": "done", "reason": "<the on-screen proof>"}\n'
        '  {"action": "fail", "reason": "<why the goal is impossible>"}'
    )


class ActionParseError(ValueError):
    """The model reply held no valid action list."""


def _validate(obj: Any) -> dict[str, Any]:
    """Validate + normalize one raw action dict. Raises ActionParseError."""
    if not isinstance(obj, dict):
        raise ActionParseError(f"action is not an object: {obj!r}")
    action = str(obj.get("action", "")).strip().lower()
    if action not in VALID_ACTIONS:
        raise ActionParseError(f"unknown action: {action!r}")
    out: dict[str, Any] = {"action": action}

    def _num(key: str, *, required: bool = True) -> float | None:
        v = obj.get(key)
        if v is None:
            if required:
                raise ActionParseError(f"{action} requires numeric {key!r}")
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            # Tolerate numeric strings — models emit "512" occasionally.
            try:
                v = float(str(v).strip())
            except (TypeError, ValueError):
                raise ActionParseError(
                    f"{action}: {key!r} is not numeric: {obj.get(key)!r}",
                ) from None
        return float(v)

    if action == "click":
        out["x"], out["y"] = _num("x"), _num("y")
        button = str(obj.get("button", "left") or "left").strip().lower()
        if button not in _BUTTONS:
            raise ActionParseError(f"click: unknown button {button!r}")
        out["button"] = button
        out["double"] = bool(obj.get("double", False))
        out["target"] = str(obj.get("target", "") or "").strip()
    elif action == "click_element":
        name = str(obj.get("name", "") or "").strip()
        if not name:
            raise ActionParseError("click_element requires a non-empty name")
        out["name"] = name
    elif action == "type":
        text = str(obj.get("text", ""))
        if not text:
            raise ActionParseError("type requires non-empty text")
        out["text"] = text
        out["clear_first"] = bool(obj.get("clear_first", False))
    elif action == "key":
        keys = obj.get("keys")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            raise ActionParseError("key requires a non-empty keys list")
        out["keys"] = [str(k) for k in keys]
    elif action == "scroll":
        direction = str(obj.get("direction", "") or "").strip().lower()
        if direction not in _SCROLL_DIRECTIONS:
            raise ActionParseError(f"scroll: unknown direction {direction!r}")
        out["direction"] = direction
        amount = obj.get("amount", DEFAULT_SCROLL_AMOUNT)
        try:
            out["amount"] = max(1, min(30, int(amount)))
        except (TypeError, ValueError):
            out["amount"] = DEFAULT_SCROLL_AMOUNT
        x = _num("x", required=False)
        y = _num("y", required=False)
        if x is not None and y is not None:
            out["x"], out["y"] = x, y
    elif action == "drag":
        out["x"], out["y"] = _num("x"), _num("y")
        out["x2"], out["y2"] = _num("x2"), _num("y2")
        try:
            out["duration_ms"] = max(
                0, min(5000, int(obj.get("duration_ms", DEFAULT_DRAG_DURATION_MS))),
            )
        except (TypeError, ValueError):
            out["duration_ms"] = DEFAULT_DRAG_DURATION_MS
    elif action in ("open_app", "switch_window"):
        name = str(obj.get("name", "") or "").strip()
        if not name:
            raise ActionParseError(f"{action} requires a non-empty name")
        out["name"] = name
    elif action == "wait":
        try:
            out["ms"] = max(0, min(MAX_WAIT_MS, int(obj.get("ms", 500))))
        except (TypeError, ValueError):
            out["ms"] = 500
    elif action in ("done", "fail"):
        out["reason"] = str(obj.get("reason", "") or "").strip()

    return out


def parse_actions(raw: str) -> list[dict[str, Any]]:
    """Parse a model reply into a validated action list.

    Fence-tolerant; accepts one object or an array. A terminal ``done``/
    ``fail`` inside a batch truncates the batch there (nothing may run after
    a terminal action). Raises :class:`ActionParseError` when nothing valid
    can be extracted — the loop counts that against the LLM-failure budget.
    """
    cleaned = (raw or "").strip()
    fence = _JSON_FENCE_RE.search(cleaned)
    if fence is not None:
        cleaned = fence.group(1).strip()
    # Find the outermost JSON payload (object or array).
    starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0]
    if not starts:
        raise ActionParseError("no JSON object/array in the model reply")
    start = min(starts)
    end = cleaned.rfind("]" if cleaned[start] == "[" else "}")
    if end <= start:
        raise ActionParseError("unterminated JSON in the model reply")
    try:
        payload = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ActionParseError(f"invalid JSON: {exc}") from exc

    raw_actions = payload if isinstance(payload, list) else [payload]
    if not raw_actions:
        raise ActionParseError("empty action array")
    actions: list[dict[str, Any]] = []
    for item in raw_actions[:MAX_BATCH]:
        validated = _validate(item)
        actions.append(validated)
        if validated["action"] in ("done", "fail"):
            break  # nothing may execute after a terminal action
    return actions
