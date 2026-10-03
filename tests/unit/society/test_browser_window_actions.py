"""Native controls keep the browser approval boundary and shown-frame identity."""

import sys
from types import SimpleNamespace

import pytest

from jarvis.society.browser.window_actions import (
    WindowClick,
    WindowKey,
    native_input,
    navigation_allowed,
    register_window_actions,
    window_message,
)
from jarvis.ui.web.society_browser_routes import validate_control
from tests.fakes.fake_browser_window import BrowserTools, native_worker


@pytest.mark.parametrize(
    "args",
    [
        {"enabled": True, "login": "true"},
        {"enabled": True, "login": 1},
        {"enabled": False, "login": True},
    ],
)
def test_signin_control_requires_explicit_boolean_takeover(args):
    with pytest.raises(ValueError, match="sign-in"):
        validate_control({"op": "takeover", "args": args})


def test_signin_control_accepts_the_inline_login_operation():
    args = {"enabled": True, "login": True}
    assert validate_control({"op": "takeover", "args": args}) == ("takeover", args)


async def test_native_window_image_is_annotated_and_never_added_during_manual_login():
    worker = native_worker()
    message = await window_message(worker)
    assert "1600 x 1000" in message["content"][0]["text"]
    assert message["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert worker.window_observation["geometry_id"] == "original"
    worker.manual = True
    assert await window_message(worker) is None


async def test_native_click_requires_observation_and_executor_permit():
    worker = native_worker()
    with pytest.raises(RuntimeError, match="Observe"):
        await native_input(worker, "click", {"x": 1480, "y": 70})
    await window_message(worker)
    worker.visual_action = False
    with pytest.raises(RuntimeError, match="approved"):
        await native_input(worker, "click", {"x": 1480, "y": 70})
    worker.visual_action = True
    worker.manual = True
    with pytest.raises(RuntimeError, match="approved"):
        await native_input(worker, "click", {"x": 1480, "y": 70})
    assert worker.native.calls == []


async def test_registered_window_actions_pass_snapshot_coordinates_and_double_click_sequence():
    worker, tools = native_worker(), BrowserTools()
    await window_message(worker)
    register_window_actions(tools, worker, SimpleNamespace)
    click, model, description = tools.actions["browser_window_click"]
    assert "profile menu" in description
    await click(model(x=1480, y=70, button="left", count=2))
    assert [args["count"] for _, args in worker.native.calls] == [1, 2]
    assert all(args["geometry_id"] == "original" for _, args in worker.native.calls)
    assert all(args["agent_input"] is True for _, args in worker.native.calls)
    worker.native.observation["geometry_id"] = "popup-moved"
    with pytest.raises(ValueError, match="geometry changed"):
        await click(model(x=1480, y=70))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_native_schema_rejects_invalid_coordinates(value):
    with pytest.raises(ValueError):
        WindowClick(x=value, y=30)


def test_native_navigation_cannot_escape_domain_restrictions_but_manual_login_can_redirect():
    assert navigation_allowed("https://x.com/home", ["x.com"], manual=False)
    assert navigation_allowed("https://login.example.com/", ["*.example.com"], manual=False)
    assert not navigation_allowed("https://x.com.evil.example/", ["x.com"], manual=False)
    assert not navigation_allowed("https://other.example/", ["x.com"], manual=False)
    assert navigation_allowed("https://accounts.google.com/", ["x.com"], manual=True)


async def test_domain_restricted_agent_never_receives_full_window_or_uses_old_observation():
    worker, tools = native_worker(), BrowserTools()
    await window_message(worker)
    worker.browser_args = {"allowed_domains": ["x.com"]}
    worker.page.url = "https://private.example/"
    assert await window_message(worker) is None
    assert worker.window_observation is None
    register_window_actions(tools, worker, SimpleNamespace)
    assert tools.actions == {}
    with pytest.raises(RuntimeError, match="Domain-restricted"):
        await native_input(worker, "click", {"x": 1480, "y": 70})
    assert worker.native.calls == []


async def test_system_file_dialog_is_manual_only_and_never_sent_to_model():
    worker = native_worker()
    await window_message(worker)
    worker.native.observation["requires_manual_control"] = True
    with pytest.raises(RuntimeError, match="operating-system dialogs"):
        await native_input(worker, "click", {"x": 400, "y": 300})
    assert await window_message(worker) is None
    assert worker.native.calls == []


async def test_native_action_cannot_reuse_an_observation_from_another_selected_tab():
    worker = native_worker()
    await window_message(worker)
    worker.page.url = "https://other.example/"
    with pytest.raises(RuntimeError, match="selected Chrome tab changed"):
        await native_input(worker, "click", {"x": 400, "y": 300})


async def test_native_action_binds_the_actual_tab_even_when_both_urls_are_identical():
    worker = native_worker()
    await window_message(worker)
    another_page = SimpleNamespace(url=worker.page.url)

    async def focused(**kwargs):
        return another_page

    worker.focused = focused
    with pytest.raises(RuntimeError, match="selected Chrome tab changed"):
        await native_input(worker, "click", {"x": 400, "y": 300})
    assert worker.native.calls == []


async def test_native_pointer_reports_only_a_successfully_approved_dispatch():
    from jarvis.society.browser.pointer import PointerTracker

    worker = native_worker()
    events = []
    worker.pointer = PointerTracker(
        "g",
        lambda kind, **data: events.append(data),
        lambda: worker.visual_action,
        lambda x, y: (x, y, 1600, 1000),
    )
    await window_message(worker)
    await native_input(worker, "click", {"x": 1480, "y": 70})
    assert events[-1]["native_window"] and events[-1]["click_id"] == 1
    assert events[-1]["x"] == 1480 and events[-1]["y"] == 70
    worker.native.observation["geometry_id"] = "changed"
    with pytest.raises(ValueError):
        await native_input(worker, "click", {"x": 1480, "y": 70})
    assert len(events) == 1


@pytest.mark.parametrize(
    "key", ["Control+o", "Control+s", "Control+v", "Shift+Insert", "Control+Shift+i", "F12", "x"]
)
def test_agent_native_keys_cannot_open_files_paste_clipboard_or_type_code(key):
    with pytest.raises(ValueError):
        WindowKey(key=key)


@pytest.mark.parametrize(
    "args", [{"button": []}, {"button": "extra"}, {"count": True}, {"count": 3}, {"geometry_id": 3}]
)
def test_control_rejects_malformed_native_input(args):
    with pytest.raises(ValueError):
        validate_control({"op": "click", "args": {"x": 10, "y": 20, **args}})


async def test_manual_worker_routes_menu_input_to_the_same_native_window():
    # The standalone worker redirects stdout on import; restore the test runner.
    previous = sys.stdout
    try:
        from jarvis.society.browser.live_runner import Worker
    finally:
        sys.stdout = previous
    worker = Worker()
    worker.native = native_worker().native
    worker.manual = True
    await worker.command("move", {"x": 1480, "y": 70})
    await worker.command("click", {"x": 1481, "y": 70, "move_only": True})
    await worker.command("click", {"x": 1480, "y": 70, "button": "right", "count": 1})
    await worker.command("text", {"text": "fixture input"})
    assert [op for op, _ in worker.native.calls] == ["move", "move", "click", "text"]
    worker.manual = False
    with pytest.raises(RuntimeError, match="Take control"):
        await worker.command("click", {"x": 1480, "y": 70})


async def test_page_only_double_click_sends_one_pair_per_viewer_click():
    previous = sys.stdout
    try:
        from jarvis.society.browser.live_runner import Worker
    finally:
        sys.stdout = previous

    class Mouse:
        def __init__(self):
            self.pairs = []

        async def click(self, x, y, *, button):
            self.pairs.extend([("down", 1), ("up", 1)])

        async def move(self, x, y):
            return None

        async def down(self, *, button, click_count):
            self.pairs.append(("down", click_count))

        async def up(self, *, button, click_count):
            self.pairs.append(("up", click_count))

    worker, mouse = Worker(), Mouse()
    worker.manual = True

    async def page():
        return SimpleNamespace(mouse=mouse)

    worker.focused = page
    await worker.command("click", {"x": 100, "y": 80, "count": 1})
    await worker.command("click", {"x": 100, "y": 80, "count": 2})
    assert mouse.pairs == [("down", 1), ("up", 1), ("down", 2), ("up", 2)]
