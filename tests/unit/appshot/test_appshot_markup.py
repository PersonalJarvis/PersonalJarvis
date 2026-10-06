"""Marking up an area in the picker — the rules, the wire, and the burnt-in result.

No display needed: the marking model is plain data, the burn-in is Pillow, and
the region flow runs against a fake picker and a recording capture service.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from jarvis.appshot import region
from jarvis.appshot import service as appshot_service
from jarvis.appshot.markup import Hide, Markup, apply_to_bytes, apply_to_rgb, parse_markup
from jarvis.appshot.picker import ACTION_DONE, EVENT_MARKING, encode
from jarvis.appshot.picker import markup_model as mm
from jarvis.screen_context.models import CaptureTarget, ScreenContext, TargetKind, TargetReason
from jarvis.screen_context.service import CaptureOutcome

SCREEN = {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0, "dpr": 1.0}


def _png(size: tuple[int, int], colour: tuple[int, int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# ------------------------------------------------------------------ the model


def test_shift_turns_a_box_into_a_square_and_a_line_into_45_degrees() -> None:
    assert mm.constrain(mm.RECT, (10, 10), (50, 30)) == (50, 50)
    assert mm.constrain(mm.ELLIPSE, (10, 10), (-20, 15)) == (-20, 40)
    x, y = mm.constrain(mm.ARROW, (0, 0), (100, 10))
    assert round(y, 6) == 0 and round(x, 3) == round((100**2 + 10**2) ** 0.5, 3)


def test_a_click_without_a_drag_draws_nothing_but_text_and_steps_count() -> None:
    assert not mm.is_meaningful(mm.Shape(mm.RECT, points=[(5, 5), (6, 6)]))
    assert not mm.is_meaningful(mm.Shape(mm.ARROW, points=[(5, 5), (6, 5)]))
    assert mm.is_meaningful(mm.Shape(mm.RECT, points=[(5, 5), (40, 30)]))
    assert not mm.is_meaningful(mm.Shape(mm.TEXT, points=[(5, 5)], text="  "))
    assert mm.is_meaningful(mm.Shape(mm.TEXT, points=[(5, 5)], text="here"))
    assert mm.is_meaningful(mm.Shape(mm.COUNTER, points=[(5, 5)], number=1))


def test_steps_number_on_from_the_highest_one() -> None:
    shapes = [mm.Shape(mm.COUNTER, points=[(0, 0)], number=n) for n in (1, 4, 2)]
    assert mm.next_counter(shapes) == 5
    assert mm.next_counter([]) == 1


def test_a_rectangle_is_picked_on_its_outline_not_its_empty_inside() -> None:
    box = mm.Shape(mm.RECT, width=2, points=[(100, 100), (300, 200)])
    assert mm.hit(box, (100, 150))
    assert mm.hit(box, (200, 201))
    assert not mm.hit(box, (200, 150))
    arrow = mm.Shape(mm.ARROW, width=3, points=[(0, 0), (100, 100)])
    assert mm.hit(arrow, (50, 52))
    assert not mm.hit(arrow, (50, 80))


def test_the_top_most_marking_wins_and_moving_keeps_the_original() -> None:
    lower = mm.Shape(mm.REDACT, mode="blur", points=[(0, 0), (100, 100)])
    upper = mm.Shape(mm.COUNTER, points=[(50, 50)], number=1, size=12)
    assert mm.shape_at([lower, upper], (50, 50)) == 1
    moved = mm.translated(upper, 10, -5)
    assert moved.points == [(60, 45)] and upper.points == [(50, 50)]


def test_undo_and_redo_walk_the_snapshots() -> None:
    history = mm.History()
    shapes = [mm.Shape(mm.RECT, points=[(0, 0), (10, 10)])]
    history.push(shapes)
    shapes.append(mm.Shape(mm.LINE, points=[(0, 0), (10, 10)]))
    history.push(shapes)
    assert [s.kind for s in history.undo() or []] == [mm.RECT]
    assert history.undo() == []
    assert history.undo() is None
    assert [s.kind for s in history.redo() or []] == [mm.RECT]
    assert history.can_redo
    history.push([])
    assert not history.can_redo


def test_snapshots_do_not_share_points_with_the_live_list() -> None:
    history = mm.History()
    shape = mm.Shape(mm.PEN, points=[(0, 0), (1, 1)])
    history.push([shape])
    history.push([])
    shape.points.append((9, 9))
    restored = history.undo()
    assert restored is not None and restored[0].points == [(0, 0), (1, 1)]


def test_the_selection_resizes_by_its_handles_and_never_flips() -> None:
    sel = (100.0, 100.0, 200.0, 100.0)
    assert mm.handle_at(sel, (300, 200)) == "se"
    assert mm.handle_at(sel, (200, 100)) == "n"
    assert mm.handle_at(sel, (200, 150)) is None
    assert mm.resize(sel, "se", (400, 260), (1920, 1080)) == (100.0, 100.0, 300.0, 160.0)
    x, y, w, h = mm.resize(sel, "w", (500, 150), (1920, 1080))
    assert w == mm.MIN_SELECTION_PX and x == 300.0 - mm.MIN_SELECTION_PX
    assert mm.resize(sel, "nw", (-50, -50), (1920, 1080))[:2] == (0.0, 0.0)


def test_the_toolbar_docks_below_then_above_then_inside_and_stays_on_screen() -> None:
    screen = (1920.0, 1080.0)
    bar = (800.0, 40.0)
    assert mm.toolbar_origin((560, 100, 800, 300), bar, screen) == (560.0, 410.0)
    assert mm.toolbar_origin((560, 700, 800, 370), bar, screen) == (560.0, 650.0)
    x, y = mm.toolbar_origin((0, 0, 1920, 1080), bar, screen)
    assert (x, y) == (560.0, 1080 - 10 - 40)
    x, _y = mm.toolbar_origin((1800, 100, 100, 100), bar, screen)
    assert x == 1920 - 800 - 10


def test_hide_patches_leave_as_fractions_of_the_area_clipped_to_it() -> None:
    sel = (100.0, 100.0, 200.0, 100.0)
    shapes = [
        mm.Shape(mm.REDACT, mode="blur", points=[(150, 120), (250, 170)]),
        mm.Shape(mm.REDACT, mode="pixelate", points=[(50, 50), (150, 150)]),
        mm.Shape(mm.RECT, points=[(150, 120), (250, 170)]),
        mm.Shape(mm.REDACT, mode="blur", points=[(0, 0), (20, 20)]),
    ]
    hides = mm.hide_fractions(shapes, sel)
    assert hides == [
        {"kind": "blur", "rect": [0.25, 0.2, 0.5, 0.5]},
        {"kind": "pixelate", "rect": [0.0, 0.0, 0.25, 0.5]},
    ]


def _measure(text: str, size: float) -> float:
    return len(text) * size * 0.5


def test_text_has_a_grip_on_every_corner_and_scales_from_the_opposite_one() -> None:
    text = mm.Shape(mm.TEXT, text="Hallo", size=20, points=[(100, 100)])
    box = mm.bounds(text, _measure)
    assert box == (100, 100, 50, 25)
    assert [name for name, _ in mm.grips(text, _measure)] == ["nw", "ne", "sw", "se"]
    # Drag the bottom-right corner down to twice the height: twice the size,
    # top-left stays put.
    bigger = mm.reshape(text, "se", (160, 150), _measure)
    assert bigger.size == 40 and bigger.points == [(100, 100)]
    # Drag the top-left corner: the bottom-right corner stays put.
    smaller = mm.reshape(text, "nw", (0, 112.5), _measure)
    assert smaller.size == 10
    x, y, w, h = mm.bounds(smaller, _measure)
    assert (x + w, y + h) == (150, 125)


def test_grips_follow_the_full_editor_rules() -> None:
    arrow = mm.Shape(mm.ARROW, points=[(0, 0), (50, 50)])
    assert mm.reshape(arrow, "to", (80, 10)).points == [(0, 0), (80, 10)]
    box = mm.Shape(mm.RECT, points=[(10, 10), (50, 50)])
    assert mm.reshape(box, "nw", (0, 0)).points == [(50, 50), (0, 0)]
    counter = mm.Shape(mm.COUNTER, points=[(0, 0)], size=12)
    assert [name for name, _ in mm.grips(counter)] == ["size"]
    assert mm.reshape(counter, "size", (30, 40)).size == 50
    assert mm.grab_scope(mm.MOVE) == "any"
    assert mm.grab_scope(mm.RECT) == "shapes"
    assert mm.grab_scope(mm.COUNTER) == "shapes"
    assert mm.grab_scope(mm.PEN) == "grips"
    assert mm.grab_scope(mm.BACKGROUND) == "none"


def test_the_middle_grip_bends_an_arrow_and_straightens_it_again() -> None:
    arrow = mm.Shape(mm.ARROW, width=3, points=[(0, 0), (100, 0)])
    assert [name for name, _ in mm.grips(arrow)] == ["from", "mid", "to"]
    curved = mm.reshape(arrow, "mid", (50, 40))
    # The curve's middle runs through the pointer.
    assert curved.bend == (50, 80)
    assert mm.segment_middle(curved) == (50, 40)
    assert mm.hit(curved, (50, 40)) and not mm.hit(curved, (50, 2))
    assert mm.bounds(curved)[3] == 40
    moved = mm.translated(curved, 10, 5)
    assert moved.bend == (60, 85)
    assert mm.reshape(curved, "mid", (51, 1)).bend is None


def test_areas_are_only_picked_when_nothing_on_them_is_hit() -> None:
    spot = mm.Shape(mm.SPOTLIGHT, points=[(0, 0), (200, 200)])
    line = mm.Shape(mm.LINE, points=[(0, 100), (200, 100)])
    assert mm.shape_at([line, spot], (100, 100)) == 0
    assert mm.shape_at([line, spot], (100, 30)) == 1
    assert mm.shape_at([line, spot], (100, 30), areas=False) is None


def test_the_picker_offers_the_full_editors_tools_keys_and_presets() -> None:
    """Both editors must stay one tool set: same keys, colours and frames."""
    import re
    from pathlib import Path

    model = (
        Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src/lib/appshotEditorModel.ts"
    ).read_text(encoding="utf-8")
    keys = dict(re.findall(r'\{ tool: "(\w+)", key: "(\w)" \}', model))
    keys.pop("crop")  # the picker's area handles are its crop
    assert {tool: key.upper() for tool, key in keys.items()} == mm.TOOL_KEYS
    presets = re.findall(r'\{ id: "(\w+)", stops: \[([^\]]+)\] \}', model)
    parsed = tuple((pid, tuple(re.findall(r'"(#[0-9a-f]{6})"', stops))) for pid, stops in presets)
    assert parsed == mm.BACKGROUND_PRESETS
    colours = re.search(r"export const COLORS = \[([^\]]+)\]", model)
    assert colours is not None
    assert tuple(c.upper() for c in re.findall(r'"(#[0-9a-fA-F]{6})"', colours.group(1))) == (
        mm.PALETTE
    )


# ---------------------------------------------------------------- the wire


def test_markup_parses_and_drops_garbage() -> None:
    overlay = _png((4, 4), (255, 0, 0, 255))
    markup = parse_markup(
        {
            "overlay": base64.b64encode(overlay).decode(),
            "hides": [
                {"kind": "blur", "rect": [0.1, 0.1, 0.2, 0.2]},
                {"kind": "erase", "rect": [0, 0, 1, 1]},
                {"kind": "pixelate", "rect": [0, 0, 0, 1]},
                {"kind": "pixelate", "rect": ["x", 0, 1, 1]},
                "nope",
            ],
        }
    )
    assert markup is not None
    assert markup.overlay_png == overlay
    assert markup.hides == (Hide("blur", (0.1, 0.1, 0.2, 0.2)),)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "x",
        {},
        {"overlay": "!!!not base64"},
        {"overlay": base64.b64encode(b"GIF89a").decode()},
    ],
)
def test_no_usable_markup_is_none(payload) -> None:
    assert parse_markup(payload) is None


def test_a_selection_carries_its_action_and_markings() -> None:
    overlay = base64.b64encode(_png((2, 2), (0, 0, 0, 0))).decode()
    selection = region.parse_selection(
        {
            "event": "selection",
            "screen": SCREEN,
            "rect": [0.1, 0.1, 0.5, 0.5],
            "action": "copy",
            "markup": {"overlay": overlay, "hides": []},
        }
    )
    assert selection is not None and selection.action == "copy"
    assert selection.markup is not None and selection.markup.overlay_png
    plain = region.parse_selection(
        {"screen": SCREEN, "rect": [0.1, 0.1, 0.5, 0.5], "action": "format-disk"}
    )
    assert plain is not None and plain.action == ACTION_DONE and plain.markup is None


def test_the_marking_event_reaches_the_reader_before_the_selection() -> None:
    lines = [
        encode({"event": "ready"}),
        encode({"event": EVENT_MARKING}),
        encode({"event": "selection", "cancelled": True}),
    ]

    class Proc:
        stdout = iter(lines)

    seen: list[str] = []
    payload = region._read_result(Proc(), lambda: seen.append("marking"))  # type: ignore[arg-type]
    assert seen == ["marking"]
    assert payload == {"event": "selection", "cancelled": True}


# ------------------------------------------------------------- the burn-in


def test_the_overlay_lands_on_the_capture_at_its_size() -> None:
    # Overlay drawn at 2x; the capture came out at 1x: it is stretched to fit.
    overlay = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    overlay.paste((255, 0, 0, 255), (0, 0, 100, 100))
    buffer = io.BytesIO()
    overlay.save(buffer, format="PNG")
    marked = apply_to_bytes(
        _jpeg((100, 50), (0, 0, 255)), "image/jpeg", Markup(overlay_png=buffer.getvalue())
    )
    with Image.open(io.BytesIO(marked)) as image:
        assert image.size == (100, 50)
        assert image.format == "JPEG"
        left = image.getpixel((20, 25))
        right = image.getpixel((80, 25))
    assert left[0] > 200 and left[2] < 60
    assert right[2] > 200 and right[0] < 60


def test_blur_and_pixelate_hide_what_was_under_them() -> None:
    picture = Image.new("RGB", (200, 100), (255, 255, 255))
    for x in range(0, 200, 2):
        for y in range(100):
            picture.putpixel((x, y), (0, 0, 0))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    markup = Markup(hides=(Hide("blur", (0.0, 0.0, 0.5, 1.0)), Hide("pixelate", (0.5, 0, 0.5, 1))))
    marked = apply_to_bytes(buffer.getvalue(), "image/png", markup)
    with Image.open(io.BytesIO(marked)) as image:
        assert image.format == "PNG"
        row = [image.getpixel((x, 50))[0] for x in range(20, 80)]
    # The 1-px stripes are gone: a flat grey instead of 0/255 alternating.
    assert max(row) - min(row) < 60


def test_a_background_frame_surrounds_the_picture() -> None:
    markup = parse_markup(
        {"background": {"preset": "ink", "padding": 0.1, "radius": 0, "shadow": False}}
    )
    assert markup is not None and markup.frame is not None
    marked = apply_to_bytes(_jpeg((200, 100), (255, 255, 255)), "image/jpeg", markup)
    with Image.open(io.BytesIO(marked)) as image:
        assert image.size == (240, 140)
        corner = image.getpixel((3, 3))
        middle = image.getpixel((120, 70))
    assert max(corner) < 40 and min(middle) > 220
    assert parse_markup({"background": {"preset": "not-a-preset"}}) is None


def test_the_thumbnail_gets_the_markings_too() -> None:
    overlay = _png((10, 10), (0, 255, 0, 255))
    size, rgb = apply_to_rgb((10, 10), bytes(300), Markup(overlay_png=overlay))
    assert size == (10, 10)
    assert rgb[:3] == bytes((0, 255, 0))


# ---------------------------------------------------------------- the flow


class Config:
    class screen_context:  # noqa: N801 - mirrors the config attribute
        enabled = True
        ttl_s = 120.0
        deck_preview_s = 0.0

    class appshot:  # noqa: N801
        target = "message"

    class ui:  # noqa: N801
        language = "de"


class Displays:
    def monitors(self):
        return [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]


class Capture:
    def __init__(self) -> None:
        self.displays = Displays()
        self.markup_at_shutter: list = []

    async def freeze_screens(self):
        return None

    async def capture(
        self, *, verdict=None, trace_id=None, region=None, master=False, frozen=None
    ):
        from jarvis.appshot.effect import shutter_markup

        self.markup_at_shutter.append(shutter_markup.get())
        context = ScreenContext(
            image=_jpeg((region[2], region[3]), (0, 0, 255)),
            mime="image/jpeg",
            size=(region[2], region[3]),
            target=CaptureTarget(
                kind=TargetKind.REGION, bbox=region, reason=TargetReason.USER_REGION
            ),
        )
        return CaptureOutcome(status="captured", verdict=verdict, context=context, handle_id="")

    def consume(self, handle_id):
        return None


@pytest.fixture
def flow(monkeypatch):
    import jarvis.screen_context.turn as turn
    from jarvis.appshot.store import get_store

    capture = Capture()
    picks: list = []
    asked: list = []

    async def pick_region(**kwargs):
        asked.append(kwargs)
        return picks.pop(0) if picks else None

    monkeypatch.setattr(turn, "get_service", lambda bus=None: capture)
    monkeypatch.setattr(appshot_service, "_load_config", lambda: Config())
    monkeypatch.setattr(region, "pick_region", pick_region)
    get_store().clear()
    yield capture, picks, asked
    get_store().clear()


async def test_a_marked_area_reaches_the_assistant_with_its_markings(flow) -> None:
    capture, picks, asked = flow
    red = Image.new("RGBA", (960, 540), (0, 0, 0, 0))
    red.paste((255, 0, 0, 255), (0, 0, 480, 540))
    buffer = io.BytesIO()
    red.save(buffer, format="PNG")
    markup = Markup(overlay_png=buffer.getvalue())
    picks.append(region.Selection(screen=SCREEN, rect=(0.25, 0.25, 0.5, 0.5), markup=markup))

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert result.ok
    assert asked == [{"language": "de"}]
    assert capture.markup_at_shutter == [markup]
    assert appshot_service.EDIT_NOTE in result.shot.note
    with Image.open(io.BytesIO(result.shot.image)) as image:
        assert image.size == (960, 540)
        assert image.getpixel((100, 270))[0] > 200
        assert image.getpixel((800, 270))[2] > 200
    pending = appshot_service.take_pending_for_turn()
    assert pending is not None and pending.image == result.shot.image


async def test_an_unmarked_area_stays_untouched(flow) -> None:
    _capture, picks, _asked = flow
    picks.append(region.Selection(screen=SCREEN, rect=(0.0, 0.0, 0.5, 0.5)))

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert result.ok
    assert appshot_service.EDIT_NOTE not in result.shot.note


async def test_copy_save_and_edit_run_after_delivery(flow, monkeypatch, tmp_path) -> None:
    _capture, picks, _asked = flow
    from jarvis.appshot import card_actions, editor_window
    from jarvis.platform import clipboard_image

    copied: list[bytes] = []
    opened: list[str] = []
    monkeypatch.setattr(clipboard_image, "write_png", lambda png: copied.append(png) or True)
    monkeypatch.setattr(
        card_actions,
        "save_to_downloads",
        lambda png: (tmp_path / "shot.png").write_bytes(png) and tmp_path / "shot.png",
    )

    async def open_window(shot_id: str) -> bool:
        opened.append(shot_id)
        return True

    monkeypatch.setattr(editor_window, "open_editor_window", open_window)
    for action in ("copy", "save", "edit"):
        picks.append(region.Selection(screen=SCREEN, rect=(0.0, 0.0, 0.5, 0.5), action=action))

    copy = await appshot_service.take_appshot(trigger="hotkey", scope="region")
    await appshot_service.take_appshot(trigger="hotkey", scope="region")
    edit = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert copy.ok and edit.ok
    assert len(copied) == 1 and copied[0].startswith(b"\x89PNG")
    assert (tmp_path / "shot.png").read_bytes().startswith(b"\x89PNG")
    assert opened == [edit.shot.id]


async def test_the_corner_card_shows_the_markings(flow, monkeypatch) -> None:
    """The card in the corner is cut at the shutter: it must show the box too."""
    import asyncio

    import jarvis.core.config as core_config
    from jarvis.appshot import effect
    from jarvis.cu.indicator import controller as indicator

    capture, picks, _asked = flow

    class CardConfig(Config):
        class appshot:  # noqa: N801
            target = "message"
            effect = True
            card_seconds = 6

    class Controller:
        thumbs: list[bytes] = []

        def hold_for_snap(self) -> None:
            pass

        async def snap(self, *, thumb_b64: str, **_kwargs) -> bool:
            self.thumbs.append(base64.b64decode(thumb_b64))
            return True

        async def snap_image(self, image_b64: str, shot_id: str = "") -> bool:
            return True

    card = Controller()
    monkeypatch.setattr(indicator, "_controller", card)
    monkeypatch.setattr(core_config, "load_config", lambda: CardConfig())
    monkeypatch.setattr(appshot_service, "_load_config", lambda: CardConfig())
    real_capture = capture.capture

    async def capture_with_shutter(**kwargs):
        # What the real Screen Context service does at the shutter boundary.
        x, y, w, h = kwargs["region"]
        effect.on_shutter(
            CaptureTarget(
                kind=TargetKind.REGION, bbox=(x, y, w, h), reason=TargetReason.USER_REGION
            ),
            (w, h),
            bytes([0, 0, 255]) * (w * h),
            Displays().monitors(),
        )
        return await real_capture(**kwargs)

    capture.capture = capture_with_shutter
    red = Image.new("RGBA", (960, 540), (0, 0, 0, 0))
    red.paste((255, 0, 0, 255), (0, 0, 480, 540))
    buffer = io.BytesIO()
    red.save(buffer, format="PNG")
    picks.append(
        region.Selection(
            screen=SCREEN, rect=(0.25, 0.25, 0.5, 0.5), markup=Markup(overlay_png=buffer.getvalue())
        )
    )

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")
    for _ in range(50):
        if card.thumbs:
            break
        await asyncio.sleep(0.02)

    assert result.ok and card.thumbs
    with Image.open(io.BytesIO(card.thumbs[0])) as thumb:
        width, height = thumb.size
        left = thumb.getpixel((width // 4, height // 2))
        right = thumb.getpixel((width * 3 // 4, height // 2))
    assert left[0] > 200 and left[2] < 60, left
    assert right[2] > 200 and right[0] < 60, right
