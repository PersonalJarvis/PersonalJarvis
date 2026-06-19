"""Hybrid native Gemini Computer-Use adapter (Wave 3).

Gemini's native ``computer_use`` tool (models ``gemini-3-flash-preview`` and
``gemini-2.5-computer-use-preview-10-2025``) is trained for precise on-screen
grounding: given a screenshot + goal it returns a predefined UI-action
``FunctionCall`` on a **0-1000 normalized grid** -- the exact grid the
screenshot-only loop already uses (``_resolve_click_pixel``). This adapter maps
those calls into the loop's own action vocabulary so the existing
``_execute_action`` backend runs them unchanged.

Design constraints (honest about the API's shape):

* Gemini CU only exposes ``ENVIRONMENT_BROWSER`` -- it is browser-oriented. The
  *generic* actions (click/type/key/scroll) work on ANY screenshot incl. the
  desktop, so we KEEP those and EXCLUDE the browser-navigation predefined
  functions (``navigate``/``go_back``/``go_forward``/``search``) plus the
  actions our loop vocabulary cannot express (``drag_and_drop``/``hover_at``).
  App launching stays on the loop's deterministic ``open_app`` path.
* This is a per-step *alternative* to the hand-rolled vision+JSON decision,
  gated behind ``[computer_use].prefer_native``. ``decide()`` returns ``None``
  on ANY failure (SDK missing, API error, unmappable action, no function call),
  so the loop falls back to the hand-rolled path for that step -- enabling this
  can never make the loop worse than the default.

The pure :func:`map_native_action` is the core and is fully unit-tested without
the live API; :class:`GeminiNativeCU` accepts an injected client for the same
reason.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Gemini models with native computer_use support (docs: ai.google.dev
#: /gemini-api/docs/computer-use, verified against google-genai 1.67).
NATIVE_CU_MODELS: frozenset[str] = frozenset(
    {"gemini-3-flash-preview", "gemini-2.5-computer-use-preview-10-2025"}
)

#: Predefined functions we exclude from the tool: browser-navigation actions
#: that have no meaning for a generic desktop, plus actions the loop vocabulary
#: cannot express. Excluding them keeps the model on the generic, desktop-safe
#: action set (click/type/key/scroll) + open_web_browser + wait.
EXCLUDED_BROWSER_FUNCTIONS: tuple[str, ...] = (
    "navigate",
    "go_back",
    "go_forward",
    "search",
    "drag_and_drop",
    "hover_at",
)


def _split_keys(combo: str) -> list[str]:
    """``"ctrl+c"`` -> ``["ctrl", "c"]``; ``"Enter"`` -> ``["enter"]``."""
    return [k.strip().lower() for k in str(combo).split("+") if k.strip()]


def map_native_action(name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Map one Gemini CU FunctionCall to a list of loop action dicts.

    Returns ``[]`` for any action the loop vocabulary cannot express, so the
    caller falls back to the hand-rolled engine for that step rather than
    guessing. The returned dicts use the loop's own grammar (same 0-1000 grid),
    so they are executed by ``_execute_action`` exactly like a parsed JSON
    action.
    """
    args = args or {}

    if name == "click_at":
        return [{"action": "click", "x": int(args["x"]), "y": int(args["y"])}]

    if name == "key_combination":
        keys = _split_keys(args.get("keys", ""))
        return [{"action": "key", "keys": keys}] if keys else []

    if name == "scroll_document":
        direction = str(args.get("direction", "")).strip().lower()
        return [{"action": "scroll", "direction": direction}] if direction else []

    if name == "scroll_at":
        direction = str(args.get("direction", "")).strip().lower()
        if not direction:
            return []
        action: dict[str, Any] = {"action": "scroll", "direction": direction}
        if args.get("magnitude") is not None:
            action["amount"] = int(args["magnitude"])
        if args.get("x") is not None and args.get("y") is not None:
            action["x"], action["y"] = int(args["x"]), int(args["y"])
        return [action]

    if name == "type_text_at":
        out: list[dict[str, Any]] = [
            {"action": "click", "x": int(args["x"]), "y": int(args["y"])}
        ]
        if args.get("clear_before_typing"):
            out.append({"action": "key", "keys": ["ctrl", "a"]})
            out.append({"action": "key", "keys": ["delete"]})
        out.append({"action": "type", "text": str(args.get("text", ""))})
        if args.get("press_enter"):
            out.append({"action": "key", "keys": ["enter"]})
        return out

    if name == "wait_5_seconds":
        return [{"action": "wait", "ms": 5000}]

    if name == "open_web_browser":
        return [{"action": "open_app", "name": "chrome"}]

    # Everything else (drag_and_drop, navigate, hover_at, search, future
    # actions) has no loop-vocabulary equivalent -> fall back.
    return []


def _first_function_call(response: Any) -> Any | None:
    """Return the first ``function_call`` part of a genai response, or None."""
    for cand in getattr(response, "candidates", None) or ():
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or ():
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "name", None):
                return fc
    return None


class GeminiNativeCU:
    """Per-step native Gemini computer_use decision engine.

    ``client`` is an object exposing ``generate(**kwargs) -> response``; a real
    one is built lazily from the API key, a fake is injected in tests. Any
    failure in :meth:`decide` returns ``None`` so the loop falls back.
    """

    def __init__(self, *, model: str, api_key: str | None = None, client: Any = None) -> None:
        self.model = model
        self._api_key = api_key
        self._client = client
        self._unavailable = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: Any) -> GeminiNativeCU | None:
        """Build the engine iff ``prefer_native`` is on AND the active provider
        is Gemini (the only provider with a native computer_use tool). Returns
        None otherwise -- the loop then uses the hand-rolled path."""
        cu = getattr(cfg, "computer_use", None)
        if cu is None or not getattr(cu, "prefer_native", False):
            return None
        primary = getattr(getattr(cfg, "brain", None), "primary", None)
        if primary != "gemini":
            return None
        model = getattr(cu, "native_model", "") or "gemini-3-flash-preview"
        return cls(model=model)

    def available(self) -> bool:
        """True if the SDK is importable and a client can be resolved."""
        if self._unavailable:
            return False
        if self._client is not None:
            return True
        try:
            import google.genai  # noqa: F401, PLC0415
        except Exception:  # noqa: BLE001
            return False
        return True

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    async def decide(
        self,
        *,
        screenshot_png: bytes,
        goal: str,
        history: list[str],
    ) -> list[dict[str, Any]] | None:
        """Ask Gemini's native computer_use tool for the next action(s).

        Returns a list of loop action dicts, or ``None`` on ANY failure so the
        caller falls back to the hand-rolled engine.
        """
        client = self._ensure_client()
        if client is None:
            return None
        try:
            response = await asyncio.to_thread(
                client.generate,
                model=self.model,
                screenshot=screenshot_png,
                goal=goal,
                history=list(history or []),
            )
        except Exception as exc:  # noqa: BLE001 — any native failure -> fall back
            log.info("[cu] native Gemini CU call failed, falling back: %s", exc)
            return None

        fc = _first_function_call(response)
        if fc is None:
            return None
        try:
            actions = map_native_action(str(fc.name), dict(getattr(fc, "args", None) or {}))
        except Exception as exc:  # noqa: BLE001 — malformed args -> fall back
            log.info("[cu] native CU action mapping failed, falling back: %s", exc)
            return None
        return actions or None

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if self._unavailable:
            return None
        try:
            self._client = _RealGeminiCUClient(api_key=self._api_key)
        except Exception as exc:  # noqa: BLE001
            log.info("[cu] native Gemini CU client unavailable: %s", exc)
            self._unavailable = True
            return None
        return self._client


class _RealGeminiCUClient:
    """Thin adapter over the google-genai client exposing ``generate(**kwargs)``.

    Kept separate (and behind a lazy import) so the module imports cleanly on a
    headless VPS without the SDK, and so :class:`GeminiNativeCU` can be unit
    tested with an injected fake instead of this.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        from google import genai  # noqa: PLC0415

        if api_key is None:
            from jarvis.core.config import get_secret  # noqa: PLC0415

            api_key = get_secret("GEMINI_API_KEY", env_fallback="GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("no Gemini API key for native computer_use")
        self._client = genai.Client(api_key=api_key)

    def generate(self, *, model: str, screenshot: bytes, goal: str, history: list[str]) -> Any:
        from google.genai import types  # noqa: PLC0415

        tool = types.Tool(
            computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER,
                excluded_predefined_functions=list(EXCLUDED_BROWSER_FUNCTIONS),
            )
        )
        history_text = "\n".join(history[-12:]) if history else "(none)"
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=screenshot, mime_type="image/png"),
                    types.Part.from_text(
                        text=(
                            f"GOAL: {goal}\nPREVIOUS_STEPS:\n{history_text}\n\n"
                            "Decide the single next UI action that advances the goal."
                        )
                    ),
                ],
            )
        ]
        return self._client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(tools=[tool], temperature=0.0),
        )
