"""Wave 3 — hybrid native Gemini Computer-Use adapter.

Gemini's native ``computer_use`` tool returns predefined UI-action FunctionCalls
on a 0-1000 normalized grid (the SAME grid the loop already uses). This adapter
maps those calls into the loop's own action vocabulary so the existing
``_execute_action`` backend runs them unchanged, and so the engine is a drop-in
per-step alternative gated behind ``[computer_use].prefer_native`` with a
hand-rolled fallback on any failure.

The pure mapping is the core and is fully testable without the live API.
"""
from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# map_native_action — Gemini CU FunctionCall -> loop action vocabulary
# ---------------------------------------------------------------------------


def test_click_at_maps_to_click() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("click_at", {"x": 300, "y": 400}) == [
        {"action": "click", "x": 300, "y": 400}
    ]


def test_key_combination_splits_into_keys_list() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("key_combination", {"keys": "ctrl+c"}) == [
        {"action": "key", "keys": ["ctrl", "c"]}
    ]


def test_key_combination_single_key_lowercased() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("key_combination", {"keys": "Enter"}) == [
        {"action": "key", "keys": ["enter"]}
    ]


def test_scroll_document_maps_to_scroll() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("scroll_document", {"direction": "down"}) == [
        {"action": "scroll", "direction": "down"}
    ]


def test_scroll_at_carries_coords_and_magnitude() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action(
        "scroll_at", {"x": 500, "y": 500, "direction": "up", "magnitude": 5}
    ) == [{"action": "scroll", "direction": "up", "amount": 5, "x": 500, "y": 500}]


def test_type_text_at_expands_to_click_then_type() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action(
        "type_text_at", {"x": 100, "y": 200, "text": "hello"}
    ) == [
        {"action": "click", "x": 100, "y": 200},
        {"action": "type", "text": "hello"},
    ]


def test_type_text_at_with_press_enter_appends_enter_key() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    actions = map_native_action(
        "type_text_at", {"x": 10, "y": 20, "text": "query", "press_enter": True}
    )
    assert actions[-1] == {"action": "key", "keys": ["enter"]}
    assert {"action": "type", "text": "query"} in actions


def test_type_text_at_with_clear_inserts_select_all_delete() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    actions = map_native_action(
        "type_text_at",
        {"x": 10, "y": 20, "text": "new", "clear_before_typing": True},
    )
    # click, then select-all + delete, then type.
    assert actions[0] == {"action": "click", "x": 10, "y": 20}
    assert {"action": "key", "keys": ["ctrl", "a"]} in actions
    assert actions[-1] == {"action": "type", "text": "new"}


def test_wait_5_seconds_maps_to_wait() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("wait_5_seconds", {}) == [
        {"action": "wait", "ms": 5000}
    ]


def test_open_web_browser_maps_to_open_app_chrome() -> None:
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action("open_web_browser", {}) == [
        {"action": "open_app", "name": "chrome"}
    ]


@pytest.mark.parametrize(
    "name,args",
    [
        ("drag_and_drop", {"x": 1, "y": 2, "destination_x": 3, "destination_y": 4}),
        ("navigate", {"url": "https://example.com"}),
        ("hover_at", {"x": 5, "y": 6}),
        ("search", {}),
        ("totally_unknown_future_action", {}),
    ],
)
def test_unsupported_actions_map_to_empty(name: str, args: dict[str, Any]) -> None:
    """Actions the loop vocabulary cannot express map to [] so decide() falls
    back to the hand-rolled engine for that step rather than guessing."""
    from jarvis.harness.native_computer_use import map_native_action

    assert map_native_action(name, args) == []


# ---------------------------------------------------------------------------
# GeminiNativeCU.from_config — enable/disable gating
# ---------------------------------------------------------------------------


class _CUCfg:
    def __init__(
        self, *, prefer_native: bool, native_model: str = "gemini-3-flash-preview"
    ) -> None:
        self.prefer_native = prefer_native
        self.native_model = native_model


class _Cfg:
    def __init__(self, *, prefer_native: bool, primary: str = "gemini") -> None:
        self.computer_use = _CUCfg(prefer_native=prefer_native)

        class _Brain:
            pass

        self.brain = _Brain()
        self.brain.primary = primary


def test_from_config_returns_none_when_disabled() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    assert GeminiNativeCU.from_config(_Cfg(prefer_native=False)) is None


def test_from_config_returns_none_when_provider_not_gemini() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    assert GeminiNativeCU.from_config(_Cfg(prefer_native=True, primary="grok")) is None


def test_from_config_builds_engine_when_enabled_and_gemini() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    eng = GeminiNativeCU.from_config(_Cfg(prefer_native=True, primary="gemini"))
    assert eng is not None
    assert eng.model == "gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# decide() — with an injected fake client (no live API)
# ---------------------------------------------------------------------------


class _FakeFunctionCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _FakePart:
    def __init__(self, fc: _FakeFunctionCall | None) -> None:
        self.function_call = fc
        self.text = None


class _FakeResponse:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": parts})()})()]


class _FakeClient:
    """Mimics the genai client surface the adapter uses: a callable that
    returns a response object with candidates[].content.parts[].function_call."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def generate(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._response


async def test_decide_maps_click_function_call() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    fc = _FakeFunctionCall("click_at", {"x": 250, "y": 750})
    fake = _FakeClient(_FakeResponse([_FakePart(fc)]))
    eng = GeminiNativeCU(model="gemini-3-flash-preview", client=fake)

    actions = await eng.decide(screenshot_png=b"\x89PNG", goal="click login", history=[])

    assert actions == [{"action": "click", "x": 250, "y": 750}]
    assert len(fake.calls) == 1


async def test_decide_returns_none_on_client_error() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    class _BoomClient:
        def generate(self, **_: Any) -> Any:
            raise RuntimeError("api down")

    eng = GeminiNativeCU(model="gemini-3-flash-preview", client=_BoomClient())

    actions = await eng.decide(screenshot_png=b"\x89PNG", goal="x", history=[])

    assert actions is None


async def test_decide_returns_none_when_no_function_call() -> None:
    from jarvis.harness.native_computer_use import GeminiNativeCU

    fake = _FakeClient(_FakeResponse([_FakePart(None)]))
    eng = GeminiNativeCU(model="gemini-3-flash-preview", client=fake)

    actions = await eng.decide(screenshot_png=b"\x89PNG", goal="x", history=[])

    assert actions is None


# ---------------------------------------------------------------------------
# Loop seam — _decide_native_batch (default no-op + native-used path)
# ---------------------------------------------------------------------------


class _FakeNative:
    def __init__(self, actions: Any) -> None:
        self._actions = actions
        self.calls: list[tuple[str, list]] = []

    async def decide(self, *, screenshot_png: bytes, goal: str, history: list) -> Any:
        self.calls.append((goal, list(history)))
        return self._actions


class _SeamCtx:
    def __init__(self, native: Any) -> None:
        self.native_cu = native
        self.per_step_timeout_s = 5.0


class _Obs:
    screenshot_path = "x.png"
    screenshot_hash = "deadbeef"


async def test_decide_native_batch_is_noop_when_disabled() -> None:
    """ctx.native_cu is None (the default) -> the seam returns None so the loop
    runs the hand-rolled path unchanged. This is the zero-regression guarantee."""
    from jarvis.harness.screenshot_only_loop import _decide_native_batch

    result = await _decide_native_batch(_SeamCtx(native=None), _Obs(), "goal", [], 1)
    assert result is None


async def test_decide_native_batch_uses_and_validates_native_actions(monkeypatch: Any) -> None:
    import jarvis.brain.router as router
    from jarvis.harness import screenshot_only_loop as loop

    async def _fake_reader(_obs: Any) -> tuple[str, str]:
        return ("image/png", "iVBORw0KGgo=")  # valid base64 (PNG header)

    monkeypatch.setattr(router, "_read_observation_image_b64", _fake_reader)
    native = _FakeNative([{"action": "click", "x": 100, "y": 200}])

    result = await loop._decide_native_batch(
        _SeamCtx(native=native), _Obs(), "click login", ["step1"], 2
    )

    # button/double normalized onto every validated click (audit #21) — additive.
    assert result == [
        {"action": "click", "x": 100, "y": 200, "button": "left", "double": False}
    ]
    assert native.calls and native.calls[0][0] == "click login"


async def test_decide_native_batch_falls_back_on_invalid_native_action(monkeypatch: Any) -> None:
    """A mapping bug that yields a malformed action must NOT reach the executor;
    the seam validates and returns None so the hand-rolled path takes over."""
    import jarvis.brain.router as router
    from jarvis.harness import screenshot_only_loop as loop

    async def _fake_reader(_obs: Any) -> tuple[str, str]:
        return ("image/png", "iVBORw0KGgo=")

    monkeypatch.setattr(router, "_read_observation_image_b64", _fake_reader)
    # 'click' without x/y is invalid per _validate_action_dict.
    native = _FakeNative([{"action": "click"}])

    result = await loop._decide_native_batch(
        _SeamCtx(native=native), _Obs(), "goal", [], 3
    )

    assert result is None
