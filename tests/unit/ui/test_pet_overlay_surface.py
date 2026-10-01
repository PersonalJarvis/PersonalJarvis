"""The desktop pet's surface contract, without a real window (docs/pets.md).

Covers what the bridge and the REST routes rely on: the pet stays up while
idle (``keeps_visible_when_idle``) and only the pet asks for status lines, the
user-hidden flag is reported the same way in-process and through the macOS
proxy, DesktopApp's pet methods update the config and degrade without a
surface, a live swap between orb looks changes the bridge's idle regime, and
the speaker control names itself to the pipeline when the pipeline asks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core import runtime_refs
from jarvis.ui.desktop_app import DesktopApp
from jarvis.ui.jarvisbar.null_overlay import NullOverlay
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessMascotOverlay
from ui.orb import controls
from ui.orb.overlay import OrbCommentBubble, OrbOverlay

# --- OrbOverlay (never started: no Tk root under pytest) --------------------------


def test_only_the_pet_stays_visible_while_idle_and_wants_status_lines() -> None:
    pet = OrbOverlay(style="pet")
    assert pet.keeps_visible_when_idle is True
    assert pet.wants_status_lines is True
    for style in ("mascot", "voice_orb"):
        other = OrbOverlay(style=style)
        assert other.keeps_visible_when_idle is False
        assert other.wants_status_lines is False


def test_the_idle_regime_follows_a_style_change() -> None:
    surface = OrbOverlay(style="mascot")
    surface.set_style("pet")  # before start(): remembered for the boot
    assert surface.keeps_visible_when_idle is True
    surface.set_style("voice_orb")
    assert surface.keeps_visible_when_idle is False


def test_the_user_hidden_flag_is_set_synchronously() -> None:
    pet = OrbOverlay(style="pet")
    assert pet.pet_user_hidden is False
    pet.set_visible(False)
    assert pet.pet_user_hidden is True
    pet.set_visible(True)
    assert pet.pet_user_hidden is False


def test_pet_calls_are_safe_before_the_window_exists() -> None:
    pet = OrbOverlay(style="pet", pet_id="none", pet_scale=9.0, pet_bubble=False)
    pet.set_pet("gigi")
    pet.set_pet_look(scale=0.1, bubble=True)
    pet.set_pet_outcome("success")
    pet.set_pet_outcome("confetti")  # unknown kind: ignored
    pet.show_status("Thinking …", "Reading the calendar")
    pet.set_muted(True)
    pet.set_speaker_muted(True)
    pet.toggle_visible()
    assert pet._pet_id == "gigi"
    assert pet._pet_scale == 0.5  # clamped into 0.5–2.0
    assert pet._pet_bubble is True
    assert pet._mic_muted is True and pet._speaker_muted is True


# --- the macOS proxy mirrors the same attributes -------------------------------------


def test_the_macos_proxy_reports_the_same_pet_attributes() -> None:
    proxy = SubprocessMascotOverlay(style="pet", pet_id="miso")
    assert proxy.keeps_visible_when_idle is True
    assert proxy.wants_status_lines is True
    assert proxy.pet_user_hidden is False
    mascot = SubprocessMascotOverlay(style="mascot")
    assert mascot.keeps_visible_when_idle is False
    assert mascot.wants_status_lines is False


def test_the_macos_proxy_forwards_the_hidden_flag() -> None:
    proxy = SubprocessMascotOverlay(style="pet")
    sent: list[dict] = []
    proxy._send = sent.append  # type: ignore[method-assign]
    proxy.set_visible(False)
    assert proxy.pet_user_hidden is True
    assert sent[-1] == {"op": "set_visible", "visible": False}
    proxy.toggle_visible()
    assert proxy.pet_user_hidden is False
    assert sent[-1] == {"op": "toggle_visible"}


# --- DesktopApp's pet methods ------------------------------------------------------------


class _PetSurface:
    """The part of the orb window DesktopApp talks to."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.pet_user_hidden = False
        self._mode = "idle"

    def set_pet(self, pet_id: str) -> None:
        self.calls.append(("set_pet", pet_id))

    def set_pet_look(self, scale, bubble) -> None:
        self.calls.append(("set_pet_look", scale, bubble))

    def set_visible(self, visible: bool) -> None:
        self.pet_user_hidden = not visible
        self.calls.append(("set_visible", visible))

    def set_style(self, style: str) -> None:
        self.calls.append(("set_style", style))

    def show(self, mode: str) -> None:
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide",))


class _Bridge:
    def __init__(self) -> None:
        self._hide_on_idle = True
        self.surface = None

    def set_surface(self, surface) -> None:
        self.surface = surface


def _app(*, orb=None, bridge=None, orb_style: str = "pet") -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)  # bypass the heavy __init__
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(
            orb_style=orb_style,
            bar_persistent=True,
            bar_accent="#e7c46e",
            orb_mascot_path="",
            pet_id="gigi",
            pet_scale=1.0,
            pet_bubble=True,
        ),
        trigger=SimpleNamespace(hotkey_pet_toggle=" alt+win+p "),
    )
    app._orb = orb
    app._bridge = bridge
    return app


def test_set_pet_updates_the_config_and_the_live_window() -> None:
    surface = _PetSurface()
    app = _app(orb=surface)
    assert app.set_pet("miso") == {"ok": True, "applied_live": True}
    assert app.cfg.ui.pet_id == "miso"
    assert surface.calls[-1] == ("set_pet", "miso")
    assert app.set_pet("") == {"ok": True, "applied_live": True}
    assert app.cfg.ui.pet_id == "none"


def test_set_pet_look_clamps_and_applies() -> None:
    surface = _PetSurface()
    app = _app(orb=surface)
    result = app.set_pet_look(scale=7.0, bubble=False)
    assert result == {"ok": True, "applied_live": True}
    assert app.cfg.ui.pet_scale == 2.0
    assert app.cfg.ui.pet_bubble is False
    assert surface.calls[-1] == ("set_pet_look", 2.0, False)


def test_pet_methods_degrade_without_a_pet_surface() -> None:
    for orb in (None, NullOverlay()):
        app = _app(orb=orb)
        assert app.set_pet("brew") == {"ok": True, "applied_live": False}
        assert app.set_pet_look(scale=1.5) == {"ok": True, "applied_live": False}
        assert app.set_pet_visible(False) == {"ok": True, "applied_live": False}
        assert app.pet_visible() is True  # nothing hid a pet
        assert app.cfg.ui.pet_id == "brew"


def test_pet_visible_reports_the_runtime_hide() -> None:
    surface = _PetSurface()
    app = _app(orb=surface)
    assert app.pet_visible() is True
    assert app.set_pet_visible(False)["applied_live"] is True
    assert app.pet_visible() is False
    app.set_pet_visible(True)
    assert app.pet_visible() is True


def test_the_idle_regime_per_style() -> None:
    app = _app()
    assert app._hide_on_idle_for("pet") is False
    assert app._hide_on_idle_for("mascot") is True
    assert app._hide_on_idle_for("voice_orb") is True
    assert app._hide_on_idle_for("jarvis_bar") is False  # bar_persistent=True
    app.cfg.ui.bar_persistent = False
    assert app._hide_on_idle_for("jarvis_bar") is True


def test_a_live_swap_to_the_pet_keeps_it_up_and_back_lets_the_mascot_hide() -> None:
    surface, bridge = _PetSurface(), _Bridge()
    app = _app(orb=surface, bridge=bridge, orb_style="mascot")
    app._surfaces = {"mascot": surface}
    result = app.swap_overlay("pet")
    assert result == {"ok": True, "applied_live": True, "style": "pet"}
    assert bridge._hide_on_idle is False
    assert ("show", "idle") in surface.calls
    surface.calls.clear()
    app.swap_overlay("mascot")
    assert bridge._hide_on_idle is True
    assert ("hide",) in surface.calls  # an idle pet does not linger as a mascot


def test_the_pet_shortcut_is_read_stripped_and_empty_when_unset() -> None:
    from jarvis.ui.desktop_app import _pet_toggle_hotkey

    assert _pet_toggle_hotkey(_app().cfg) == "alt+win+p"
    assert _pet_toggle_hotkey(SimpleNamespace(trigger=SimpleNamespace())) == ""
    assert _pet_toggle_hotkey(SimpleNamespace()) == ""


# --- the speaker control names itself to the pipeline -------------------------------------


class _SourcePipeline:
    def __init__(self) -> None:
        self.volume = 0.8
        self.sources: list[str] = []

    def get_tts_volume(self) -> float:
        return self.volume

    def set_tts_volume(self, volume: float, *, source: str = "") -> None:
        self.volume = float(volume)
        self.sources.append(source)


class _PlainPipeline:
    def __init__(self) -> None:
        self.volume = 0.8

    def get_tts_volume(self) -> float:
        return self.volume

    def set_tts_volume(self, volume: float) -> None:
        self.volume = float(volume)


@pytest.fixture
def live_pipeline():
    holder: dict[str, object] = {}

    def install(pipeline: object) -> object:
        runtime_refs.set_speech_pipeline(pipeline)
        holder["p"] = pipeline
        return pipeline

    try:
        yield install
    finally:
        runtime_refs.set_speech_pipeline(None)


def test_the_speaker_toggle_passes_its_source_when_the_pipeline_takes_one(live_pipeline) -> None:
    pipeline = live_pipeline(_SourcePipeline())
    assert controls.toggle_speaker_mute(source="pet") is True
    assert controls.toggle_speaker_mute(source="pet") is False
    assert pipeline.sources == ["pet", "pet"]
    assert pipeline.volume == pytest.approx(0.8)


def test_the_speaker_toggle_still_works_with_an_older_setter(live_pipeline) -> None:
    pipeline = live_pipeline(_PlainPipeline())
    assert controls.toggle_speaker_mute(source="pet") is True
    assert pipeline.volume == 0.0
    assert controls.toggle_speaker_mute() is False
    assert pipeline.volume == pytest.approx(0.8)


@pytest.mark.parametrize("style,source", [("pet", "pet"), ("voice_orb", "orb")])
def test_the_macos_speaker_disc_names_its_source(live_pipeline, style: str, source: str) -> None:
    pipeline = live_pipeline(_SourcePipeline())
    proxy = SubprocessMascotOverlay(style=style)
    proxy._dispatch_speaker_toggle()  # noqa: SLF001 — the child's speaker event
    assert pipeline.sources == [source]


# --- dragging moves the status bubble without repainting it ---------------------------


class _RecordingTop:
    def __init__(self) -> None:
        self.geometries: list[str] = []

    def geometry(self, spec: str) -> None:
        self.geometries.append(spec)


def test_a_drag_only_moves_the_painted_status_bubble() -> None:
    bubble = OrbCommentBubble.__new__(OrbCommentBubble)  # no Tk: geometry only
    top = _RecordingTop()
    bubble._top = top  # noqa: SLF001
    bubble._screen_w = 1920  # noqa: SLF001
    bubble._status_showing = True  # noqa: SLF001
    bubble._status_size = (240, 60)  # noqa: SLF001
    repaints: list[bool] = []
    bubble._render_status = lambda: repaints.append(True) or True  # type: ignore[method-assign]

    # (center_x, below_y, above_y, limit_bottom): room below the strip.
    bubble.move_status((500, 300, 100, 1000))
    # No room below: the bubble sits over the figure instead.
    bubble.move_status((500, 980, 700, 1000))

    assert top.geometries == ["+380+300", "+380+640"]
    assert repaints == []


# --- the status card: layout, clear and fade, forwarding ----------------------------------


def _card() -> OrbCommentBubble:
    bubble = OrbCommentBubble.__new__(OrbCommentBubble)  # no Tk: geometry only
    bubble._screen_w = 1920  # noqa: SLF001
    bubble._ui_scale = 1.0  # noqa: SLF001
    bubble._status_max_w = None  # noqa: SLF001
    bubble._status_expanded = False  # noqa: SLF001
    return bubble


def _chars(text: str) -> int:
    return 7 * len(text)


def test_a_short_status_is_a_compact_full_pill() -> None:
    bubble = _card()
    width, height, title, detail, radius = bubble.card_layout(
        "Thinking", "Reading the calendar", _chars, _chars, 18, 16
    )
    assert title == ["Thinking"] and detail == ["Reading the calendar"]
    assert width < 380  # sized to its text, not to a fixed width
    assert radius == height // 2  # two lines collapsed: fully rounded ends


def test_long_text_is_clipped_with_an_ellipsis_and_capped_by_the_strip() -> None:
    bubble = _card()
    bubble.set_status_max_width(300)
    long = "word " * 80
    width, _height, title, detail, _radius = bubble.card_layout(long, long, _chars, _chars, 18, 16)
    assert 250 <= width <= 300  # the cap; the ellipsised line sets the rest
    assert len(title) == 1 and title[0].endswith("…")
    assert len(detail) == 1 and detail[0].endswith("…")


def test_a_click_expands_the_detail_to_three_lines_keeping_the_end_radius() -> None:
    bubble = _card()
    long = "word " * 80
    collapsed = bubble.card_layout("Title", long, _chars, _chars, 18, 16)
    bubble._status_expanded = True  # noqa: SLF001
    expanded = bubble.card_layout("Title", long, _chars, _chars, 18, 16)
    assert len(expanded[3]) == 3
    assert expanded[1] > collapsed[1]
    assert expanded[4] == collapsed[4]


class _FakeTop:
    """Just enough of a Toplevel for the clear/fade timers."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[int, object]] = {}
        self.alphas: list[float] = []
        self.withdrawn = 0
        self._next = 0

    def after(self, ms: int, fn: object) -> str:
        self._next += 1
        key = f"after#{self._next}"
        self.pending[key] = (ms, fn)
        return key

    def after_cancel(self, key: str) -> None:
        self.pending.pop(key, None)

    def wm_attributes(self, name: str, value: float) -> None:
        assert name == "-alpha"
        self.alphas.append(value)

    def withdraw(self) -> None:
        self.withdrawn += 1

    def run_next(self) -> int:
        key = next(iter(self.pending))
        ms, fn = self.pending.pop(key)
        fn()  # type: ignore[operator]
        return ms


def _showing_card() -> tuple[OrbCommentBubble, _FakeTop]:
    bubble = _card()
    top = _FakeTop()
    bubble._top = top  # noqa: SLF001
    bubble._canvas = object()  # noqa: SLF001
    bubble._status_showing = True  # noqa: SLF001
    bubble._status_size = (240, 52)  # noqa: SLF001
    bubble._status = ("Thinking", "")  # noqa: SLF001
    bubble._status_anchor = (500, 300, 100, 1000)  # noqa: SLF001
    bubble._clear_after_id = None  # noqa: SLF001
    bubble._fade_after_id = None  # noqa: SLF001
    bubble._status_clear_pending = False  # noqa: SLF001
    bubble._dismiss_after_id = None  # noqa: SLF001
    bubble._queue_after_id = None  # noqa: SLF001
    bubble._queued_text = None  # noqa: SLF001
    bubble._render_status = lambda: True  # type: ignore[method-assign]
    return bubble, top


def test_clear_lingers_then_fades_then_withdraws() -> None:
    bubble, top = _showing_card()
    bubble.clear_status(1.5)
    assert top.run_next() == 1500  # the linger
    while top.pending:
        top.run_next()  # the fade steps
    fade = top.alphas[:-1]
    assert fade and all(a > b for a, b in zip(fade, fade[1:], strict=False))
    assert top.alphas[-1] == 1.0  # opacity restored for the next card
    assert top.withdrawn == 1
    assert bubble.status_showing is False


def test_a_new_status_cancels_a_pending_clear() -> None:
    bubble, top = _showing_card()
    bubble.clear_status(1.5)
    assert top.pending
    bubble._cancel_timers = lambda: None  # type: ignore[method-assign]
    bubble.show_status("Searching the web", "", anchor=(500, 300, 100, 1000))
    assert not top.pending
    assert bubble.status_showing is True


def test_clear_with_no_linger_hides_at_once() -> None:
    bubble, top = _showing_card()
    bubble.clear_status(0)
    assert top.withdrawn == 1 and not top.pending


def test_a_repeated_clear_keeps_the_first_deadline() -> None:
    bubble, top = _showing_card()
    bubble.clear_status(1.5)
    bubble.clear_status(1.5)
    assert len(top.pending) == 1


def test_the_pet_surface_accepts_clear_before_the_window_exists() -> None:
    pet = OrbOverlay(style="pet")
    pet.clear_status()
    pet.clear_status(0)
    pet.show_listening_transcript("what the user said")  # never echoed on the pet
    OrbOverlay(style="mascot").clear_status()  # other looks ignore it


def test_the_macos_proxy_forwards_title_detail_and_clear() -> None:
    proxy = SubprocessMascotOverlay(style="pet")
    sent: list[dict] = []
    proxy._send = sent.append  # type: ignore[method-assign]
    proxy.show_status("Planning the answer", "Comparing two options")
    proxy.clear_status(2.0)
    assert sent == [
        {"op": "show_status", "title": "Planning the answer", "detail": "Comparing two options"},
        {"op": "clear_status", "linger_s": 2.0},
    ]


class _StatusSurface:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def show_status(self, title: str, detail: str) -> None:
        self.calls.append(("show_status", title, detail))

    def clear_status(self, linger_s: float) -> None:
        self.calls.append(("clear_status", linger_s))


def test_the_host_dispatches_status_ops_including_the_old_keys() -> None:
    from jarvis.ui.jarvisbar import host

    surface = _StatusSurface()
    host.dispatch(surface, {"op": "show_status", "title": "A", "detail": "b"})
    host.dispatch(surface, {"op": "show_status", "header": "Old", "line": "keys"})
    host.dispatch(surface, {"op": "clear_status", "linger_s": 0.5})
    host.dispatch(surface, {"op": "clear_status"})
    assert surface.calls == [
        ("show_status", "A", "b"),
        ("show_status", "Old", "keys"),
        ("clear_status", 0.5),
        ("clear_status", 1.5),
    ]


# --- The control strip shows only while it is useful -------------------------


class _Strip:
    """Records what the overlay asks of its pet strip."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pointer_inside = False

    def set_state(self, **_kwargs: object) -> None:
        pass

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def hide_after_grace(self) -> None:
        self.calls.append("grace")


def _pet_with_strip(pet_id: str = "gigi") -> tuple[OrbOverlay, _Strip]:
    pet = OrbOverlay(style="pet", pet_id=pet_id)
    strip = _Strip()
    pet._controls = strip  # type: ignore[assignment]
    pet._window_mapped = lambda: True  # type: ignore[method-assign]
    return pet, strip


def test_an_idle_pet_shows_only_the_figure() -> None:
    pet, strip = _pet_with_strip()
    pet._mode = "idle"
    assert pet._pet_strip_wanted() is False
    pet._sync_controls_visibility()
    assert strip.calls == ["grace"]


@pytest.mark.parametrize("mode", ["listen", "think", "speak", "dictate", "dictate_transcribing"])
def test_the_strip_joins_the_pet_while_jarvis_is_engaged(mode: str) -> None:
    pet, strip = _pet_with_strip()
    pet._mode = mode
    pet._sync_controls_visibility()
    assert strip.calls == ["show"]


def test_hovering_the_pet_brings_the_strip_and_leaving_lets_it_go() -> None:
    pet, strip = _pet_with_strip()
    pet._mode = "idle"
    pet._on_orb_pointer_enter()
    assert strip.calls == ["show"]
    assert pet._pet_strip_wanted() is True
    pet._on_orb_pointer_leave()
    assert strip.calls == ["show", "grace"]
    assert pet._pet_strip_wanted() is False


def test_the_pointer_on_the_strip_keeps_it_up() -> None:
    pet, strip = _pet_with_strip()
    pet._mode = "idle"
    strip.pointer_inside = True
    assert pet._pet_strip_wanted() is True


def test_the_figureless_pet_always_keeps_its_strip() -> None:
    pet, strip = _pet_with_strip(pet_id="none")
    pet._mode = "idle"
    pet._sync_controls_visibility()
    assert strip.calls == ["show"]


def test_a_hidden_pet_hides_its_strip_even_mid_conversation() -> None:
    pet, strip = _pet_with_strip()
    pet._mode = "speak"
    pet._window_mapped = lambda: False  # type: ignore[method-assign]
    pet._sync_controls_visibility()
    assert strip.calls == ["hide"]
