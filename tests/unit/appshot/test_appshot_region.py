"""Area appshots — selection geometry, the region flow, the two shortcuts, REST.

Everything runs without a display: the picker process is replaced by a fake
``pick_region`` and the capture service by a recorder, so these pass on a
headless CI box exactly as on a desktop.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.appshot import region
from jarvis.appshot import service as appshot_service
from jarvis.appshot.hotkey import AppshotShortcut, configured_hotkeys
from jarvis.appshot.picker import EVENT_SELECTION, decode, encode
from jarvis.screen_context.models import (
    CaptureTarget,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.ports import CaptureUnavailable
from jarvis.screen_context.service import CaptureOutcome
from jarvis.screen_context.targeting import region_target

# mss shape: [0] is the virtual desktop. A 4K laptop panel at 150 % on the
# left of a 1080p monitor, as Windows reports them in physical pixels.
MONITORS = [
    {"left": -3840, "top": 0, "width": 5760, "height": 2160},
    {"left": -3840, "top": 0, "width": 3840, "height": 2160},
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
]


# ----------------------------------------------------------------- geometry


def test_a_drag_in_any_direction_becomes_the_same_fractions() -> None:
    forward = region.selection_fractions(100, 50, 300, 250, 1000, 500)
    backward = region.selection_fractions(300, 250, 100, 50, 1000, 500)
    assert forward == backward == (0.1, 0.1, 0.2, 0.4)


def test_a_click_or_a_slip_is_not_a_selection() -> None:
    assert region.selection_fractions(10, 10, 12, 200, 1000, 500) is None
    assert region.selection_fractions(10, 10, 10, 10, 1000, 500) is None


def test_a_drag_past_the_screen_edge_is_clamped() -> None:
    assert region.selection_fractions(-50, -50, 2000, 600, 1000, 500) == (0.0, 0.0, 1.0, 1.0)


def test_a_mixed_dpi_screen_maps_to_its_physical_monitor() -> None:
    # Qt 6 on Windows: native origin, logical size (3840 / 1.5 = 2560).
    laptop = {"x": -3840.0, "y": 0.0, "w": 2560.0, "h": 1440.0, "dpr": 1.5}
    selection = region.Selection(screen=laptop, rect=(0.5, 0.5, 0.25, 0.25))

    assert region.selection_to_bbox(selection, MONITORS) == (-1920, 1080, 960, 540)


def test_a_mac_screen_in_points_maps_one_to_one() -> None:
    monitors = [
        {"left": 0, "top": 0, "width": 1512, "height": 982},
        {"left": 0, "top": 0, "width": 1512, "height": 982},
    ]
    screen = {"x": 0.0, "y": 0.0, "w": 1512.0, "h": 982.0, "dpr": 2.0}
    selection = region.Selection(screen=screen, rect=(0.0, 0.0, 0.5, 0.5))

    assert region.selection_to_bbox(selection, monitors) == (0, 0, 756, 491)


def test_no_monitors_means_no_bbox() -> None:
    screen = {"x": 0, "y": 0, "w": 1, "h": 1, "dpr": 1}
    selection = region.Selection(screen=screen, rect=(0, 0, 1, 1))
    assert region.selection_to_bbox(selection, []) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"event": EVENT_SELECTION, "cancelled": True},
        {"event": EVENT_SELECTION, "screen": {"x": 0}, "rect": [0, 0, 1, 1]},
        {"event": EVENT_SELECTION, "screen": {"x": 0, "y": 0, "w": 1, "h": 1, "dpr": 1}},
        {
            "event": EVENT_SELECTION,
            "screen": {"x": 0, "y": 0, "w": 1, "h": 1, "dpr": 1},
            "rect": [0, 0, 0, 1],
        },
    ],
)
def test_a_cancelled_or_garbled_selection_is_none(payload) -> None:
    assert region.parse_selection(payload) is None


def test_the_wire_round_trips_one_line() -> None:
    line = encode({"event": EVENT_SELECTION, "rect": [0.1, 0.2, 0.3, 0.4]})
    assert line.endswith("\n") and line.count("\n") == 1
    assert decode(line) == {"event": EVENT_SELECTION, "rect": [0.1, 0.2, 0.3, 0.4]}
    assert decode("not json") is None


# ---------------------------------------------------------------- targeting


def test_the_region_target_is_clipped_to_its_monitor() -> None:
    target = region_target((1800, 1000, 400, 300), monitors=MONITORS, window=None)

    assert target.kind is TargetKind.REGION
    assert target.reason is TargetReason.USER_REGION
    assert target.bbox == (1800, 1000, 120, 80)
    assert target.window_handle is None


def test_a_sliver_is_refused_rather_than_captured() -> None:
    with pytest.raises(CaptureUnavailable):
        region_target((1919, 0, 300, 300), monitors=MONITORS, window=None)
    with pytest.raises(CaptureUnavailable):
        region_target((0, 0, 100, 100), monitors=[], window=None)


# --------------------------------------------------------------- the flow


class Config:
    class screen_context:  # noqa: N801 - mirrors the config attribute
        enabled = True
        ttl_s = 120.0
        deck_preview_s = 120.0

    class appshot:  # noqa: N801
        target = "message"


class Displays:
    def monitors(self):
        return list(MONITORS)


class RegionCaptureService:
    def __init__(self) -> None:
        self.regions: list = []
        self.displays = Displays()

    async def capture(self, *, verdict=None, trace_id=None, region=None):
        self.regions.append((verdict, region))
        context = ScreenContext(
            image=b"jpeg",
            mime="image/jpeg",
            size=(region[2], region[3]),
            target=CaptureTarget(
                kind=TargetKind.REGION,
                bbox=region,
                reason=TargetReason.USER_REGION,
                monitor_name="2",
                window=WindowFacts(app_name="Editor", title="notes.md"),
            ),
        )
        return CaptureOutcome(status="captured", verdict=verdict, context=context, handle_id="h")

    def consume(self, handle_id):
        return None


@pytest.fixture
def flow(monkeypatch):
    import jarvis.screen_context.turn as turn
    from jarvis.appshot.store import get_store

    service = RegionCaptureService()
    picks: list = []

    async def pick_region(**_kwargs):
        return picks.pop(0) if picks else None

    monkeypatch.setattr(turn, "get_service", lambda bus=None: service)
    monkeypatch.setattr(appshot_service, "_load_config", lambda: Config())
    monkeypatch.setattr(region, "pick_region", pick_region)
    get_store().clear()
    yield service, picks
    get_store().clear()


async def test_an_area_appshot_captures_exactly_the_selected_rectangle(flow) -> None:
    service, picks = flow
    picks.append(
        region.Selection(
            screen={"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0, "dpr": 1.0},
            rect=(0.25, 0.5, 0.5, 0.25),
        )
    )

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert result.ok
    verdict, bbox = service.regions[0]
    assert bbox == (480, 540, 960, 270)
    assert verdict.intent is VisualIntent.SCREEN
    assert result.shot.label == "selected area"
    assert result.shot.delivered_to == "message"


async def test_esc_on_the_picker_takes_nothing(flow) -> None:
    service, _picks = flow

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert not result.ok
    assert result.reason_code == "cancelled"
    assert service.regions == []


async def test_a_host_without_a_picker_refuses_honestly(flow, monkeypatch) -> None:
    service, _picks = flow

    async def unavailable(**_kwargs):
        raise region.RegionUnavailable("An area cannot be selected here: no screen.")

    monkeypatch.setattr(region, "pick_region", unavailable)
    result = await appshot_service.take_appshot(trigger="button", scope="region")

    assert result.reason_code == "region_unavailable"
    assert "no screen" in result.message
    assert service.regions == []


async def test_the_window_scope_never_opens_the_picker(flow, monkeypatch) -> None:
    async def must_not_run(**_kwargs):
        raise AssertionError("the front-window appshot must not ask for an area")

    monkeypatch.setattr(region, "pick_region", must_not_run)
    service, _picks = flow

    async def window_capture(*, verdict=None, trace_id=None, region=None):
        assert region is None
        return CaptureOutcome(status="refused", verdict=verdict, message="nope")

    service.capture = window_capture
    result = await appshot_service.take_appshot(trigger="hotkey")
    assert result.status == "refused"


# ---------------------------------------------------------------- shortcuts


def test_both_shortcuts_are_read_and_normalized() -> None:
    class Block:
        hotkey = "Left_Alt + Right_Alt"
        region_hotkey = " Alt+Win+A "

    assert configured_hotkeys(Block()) == {"window": "alt+alt", "region": "alt+win+a"}

    class OldConfig:
        hotkey = "alt+alt"

    assert configured_hotkeys(OldConfig())["region"] == "", "an old config arms no area key"


class _Instance:
    owns_ambient_duties = True


async def _reload_with(monkeypatch, hotkey: str, region_hotkey: str) -> AppshotShortcut:
    import jarvis.appshot.hotkey as hotkey_module
    import jarvis.core.config as config_module
    import jarvis.core.instance as instance_module
    import jarvis.platform.probes as probes

    class Cfg:
        class appshot:  # noqa: N801
            pass

    Cfg.appshot.hotkey = hotkey
    Cfg.appshot.region_hotkey = region_hotkey
    monkeypatch.setattr(config_module, "load_config", lambda: Cfg)
    monkeypatch.setattr(instance_module, "current_instance", lambda: _Instance())
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    armed: list[dict] = []

    async def run_combos(self, combos):
        armed.append(dict(combos))

    monkeypatch.setattr(hotkey_module.AppshotShortcut, "_run_combos", run_combos)
    shortcut = AppshotShortcut(bus=object())
    await shortcut.reload()
    if shortcut._trigger_task is not None:
        await shortcut._trigger_task
    shortcut.armed_combos = armed  # type: ignore[attr-defined]
    return shortcut


async def test_two_combos_share_one_listener(monkeypatch) -> None:
    shortcut = await _reload_with(monkeypatch, "ctrl+alt+a", "alt+win+a")

    assert shortcut.status_for("window").armed
    assert shortcut.status_for("region").armed
    assert shortcut.armed_combos == [{"window": "ctrl+alt+a", "region": "alt+win+a"}]


async def test_the_same_key_for_both_arms_only_the_window(monkeypatch) -> None:
    shortcut = await _reload_with(monkeypatch, "ctrl+alt+a", "ctrl+alt+a")

    assert shortcut.status_for("window").armed
    region_status = shortcut.status_for("region")
    assert not region_status.armed
    assert "front window" in region_status.detail
    assert shortcut.armed_combos == [{"window": "ctrl+alt+a"}]


async def test_an_empty_area_shortcut_is_simply_off(monkeypatch) -> None:
    shortcut = await _reload_with(monkeypatch, "ctrl+alt+a", "")

    assert shortcut.status_for("region").hotkey == ""
    assert not shortcut.status_for("region").armed
    assert shortcut.armed_combos == [{"window": "ctrl+alt+a"}]


async def test_a_cancelled_area_pick_publishes_no_refusal(monkeypatch) -> None:
    class Bus:
        def __init__(self) -> None:
            self.events: list = []

        async def publish(self, event) -> None:
            self.events.append(event)

    async def cancelled(**kwargs):
        assert kwargs["scope"] == "region"
        return appshot_service.AppshotResult(status="refused", reason_code="cancelled")

    monkeypatch.setattr(appshot_service, "take_appshot", cancelled)
    bus = Bus()
    await AppshotShortcut(bus)._take("region")
    assert bus.events == []


# --------------------------------------------------------------------- REST


@pytest.fixture
def client() -> TestClient:
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = None
    return TestClient(app)


def test_take_passes_the_area_scope_through(client, monkeypatch) -> None:
    seen: dict = {}

    async def take(**kwargs):
        seen.update(kwargs)
        return appshot_service.AppshotResult(
            status="refused", reason_code="cancelled", message="No area was selected."
        )

    monkeypatch.setattr(appshot_service, "take_appshot", take)
    body = client.post("/api/appshot/take", json={"scope": "region"}).json()

    assert seen["scope"] == "region" and seen["trigger"] == "button"
    assert body == {"ok": False, "reason": "cancelled", "message": "No area was selected."}


def test_an_unknown_scope_is_refused(client) -> None:
    assert client.post("/api/appshot/take", json={"scope": "desktop"}).status_code == 422


def test_one_key_for_both_shortcuts_is_refused_before_writing(client, monkeypatch) -> None:
    import jarvis.core.config as config_module
    import jarvis.core.config_writer as writer

    class Cfg:
        class appshot:  # noqa: N801
            hotkey = "alt+alt"
            region_hotkey = "alt+win+a"

    writes: list = []
    monkeypatch.setattr(config_module, "load_config", lambda: Cfg)
    monkeypatch.setattr(writer, "set_appshot_settings", lambda values: writes.append(values))

    response = client.put("/api/appshot/settings", json={"region_hotkey": "Alt+Alt"})

    assert response.status_code == 400
    assert "two different shortcuts" in response.json()["detail"]
    assert writes == []


# ----------------------------------------------------------- picker outcomes


@pytest.fixture
def picker_host(monkeypatch):
    monkeypatch.setattr(region, "picker_capability", lambda: (True, ""))
    monkeypatch.setattr(region, "_SETTLE_S", 0.0)
    region._picking = False
    yield monkeypatch
    region._picking = False


def _runs(monkeypatch, outcome) -> None:
    async def run_picker(_timeout_s):
        return outcome

    monkeypatch.setattr(region, "_run_picker", run_picker)


async def test_a_crashed_picker_is_reported_not_taken_for_a_cancel(picker_host) -> None:
    _runs(picker_host, (None, 1, False))
    with pytest.raises(region.RegionUnavailable, match="stopped unexpectedly"):
        await region.pick_region()


async def test_a_picker_without_a_screen_says_so(picker_host) -> None:
    from jarvis.appshot.picker import EXIT_NO_GUI

    _runs(picker_host, (None, EXIT_NO_GUI, False))
    with pytest.raises(region.RegionUnavailable, match="no usable screen"):
        await region.pick_region()


@pytest.mark.parametrize("outcome", [(None, 0, False), (None, 1, True)])
async def test_a_clean_exit_or_a_timeout_is_a_quiet_cancel(picker_host, outcome) -> None:
    _runs(picker_host, outcome)
    assert await region.pick_region() is None


async def test_a_second_picker_is_refused_while_one_is_open(picker_host) -> None:
    import asyncio

    release = asyncio.Event()

    async def run_picker(_timeout_s):
        await release.wait()
        return (None, 0, False)

    picker_host.setattr(region, "_run_picker", run_picker)
    first = asyncio.create_task(region.pick_region())
    await asyncio.sleep(0)
    with pytest.raises(region.RegionUnavailable, match="already being selected"):
        await region.pick_region()
    release.set()
    assert await first is None
    assert region._picking is False, "the gate opens again after the first pick"


async def test_overlapping_reloads_leave_exactly_one_listener(monkeypatch) -> None:
    import asyncio

    import jarvis.appshot.hotkey as hotkey_module
    import jarvis.core.config as config_module
    import jarvis.core.instance as instance_module
    import jarvis.platform.probes as probes

    class Cfg:
        class appshot:  # noqa: N801
            hotkey = "ctrl+alt+a"
            region_hotkey = "alt+win+a"

    monkeypatch.setattr(config_module, "load_config", lambda: Cfg)
    monkeypatch.setattr(instance_module, "current_instance", lambda: _Instance())
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    running: list[int] = []

    async def run_combos(self, combos):
        running.append(1)
        try:
            await asyncio.Event().wait()
        finally:
            running.pop()

    monkeypatch.setattr(hotkey_module.AppshotShortcut, "_run_combos", run_combos)
    shortcut = AppshotShortcut(bus=object())
    await asyncio.gather(shortcut.reload(), shortcut.reload(), shortcut.reload())
    await asyncio.sleep(0)
    assert len(running) == 1
    await shortcut.stop()
    await asyncio.sleep(0)
    assert running == []
