"""End-to-end service behaviour, with fakes — no display, no permissions.

This file is where the feature's promises are actually pinned:

* **one** capture per trigger, never two, never a loop;
* an ambiguous turn asks and captures nothing;
* a denylisted window is never captured *at all* (not captured-then-filtered);
* a stored capture is consumable once, then it is gone.

Fakes rather than mocks, per the repo convention (``tests/fakes/``): each one
records what it was asked for, so "the cursor was read exactly once" is an
assertion about behaviour rather than about call plumbing.
"""
from __future__ import annotations

import asyncio
import io
import secrets
import threading

from jarvis.screen_context.models import (
    DegradationCode,
    RedactionRule,
    TargetKind,
    TargetReason,
    WindowFacts,
)
from jarvis.screen_context.ports import (
    CapturePermissionIssue,
    CaptureUnavailable,
    WindowSnapshot,
)
from jarvis.screen_context.service import (
    ScreenContextService,
    ScreenContextSettings,
    _encode,
    settings_from_config,
)

MONITORS = [
    {"left": -1920, "top": 0, "width": 3840, "height": 1080, "name": "virtual"},
    {"left": -1920, "top": 0, "width": 1920, "height": 1080, "name": "left"},
    {"left": 0, "top": 0, "width": 1920, "height": 1080, "name": "primary"},
]


class FakeCursor:
    name = "fake-cursor"

    def __init__(self, point=(-500, 400)) -> None:
        self._point = point
        self.reads = 0

    def position(self):
        self.reads += 1
        return self._point


class FakeBar:
    name = "fake-bar"

    def __init__(self, point=(900, 1000)) -> None:
        self._point = point
        self.reads = 0

    def position(self):
        self.reads += 1
        return self._point


class FakeDisplays:
    name = "fake-displays"

    def __init__(self, monitors=None) -> None:
        self._monitors = MONITORS if monitors is None else monitors

    def monitors(self):
        return list(self._monitors)


class FakeWindowProbe:
    name = "fake-window"

    def __init__(self, facts: WindowFacts | None = None, handle: int | None = 7) -> None:
        self._facts = (
            facts
            if facts is not None
            else WindowFacts(
                app_name="editor", title="notes.md", pid=42, frame_rect=(10, 10, 800, 600)
            )
        )
        self._handle = handle

    def foreground(self):
        return self._facts

    def foreground_handle(self):
        return self._handle


class FakeCapturer:
    """Returns a solid-white frame and counts every grab."""

    name = "fake-capture"

    def __init__(self, *, fail: Exception | None = None, scale: int = 1) -> None:
        self.grabs: list[tuple] = []
        self._fail = fail
        self._scale = scale

    def grab(self, bbox, *, window_handle=None):
        self.grabs.append((tuple(bbox), window_handle))
        if self._fail is not None:
            raise self._fail
        width = int(bbox[2]) * self._scale
        height = int(bbox[3]) * self._scale
        # Keep the fake frame small enough to encode quickly.
        width, height = min(width, 320), min(height, 240)
        return ((width, height), b"\xff" * (width * height * 3))


class FakeNode:
    def __init__(self, name="", value="", bounds=(0, 0, 0, 0), is_password=False):
        self.name = name
        self.value = value
        self.bounds = bounds
        self.is_password = is_password
        self.role = "Text"


class FakeObservation:
    def __init__(
        self,
        nodes=(),
        *,
        active_pid: int = 42,
        window_title: str = "notes.md",
    ) -> None:
        self.nodes = tuple(nodes)
        self.active_pid = active_pid
        self.window_title = window_title


class FakeTextReader:
    name = "fake-text"

    def __init__(self, observation=None) -> None:
        self._observation = observation
        self.reads = 0

    async def read(self, *, window_title_filter=None):
        self.reads += 1
        return self._observation


class RecordingBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


def make_service(**overrides) -> ScreenContextService:
    kwargs = {
        "settings": ScreenContextSettings(),
        "cursor": FakeCursor(),
        "bar": FakeBar(),
        "displays": FakeDisplays(),
        "window_probe": FakeWindowProbe(),
        "capturer": FakeCapturer(),
        "ui_text_reader": FakeTextReader(FakeObservation()),
        "permission_probe": lambda: None,
    }
    kwargs.update(overrides)
    return ScreenContextService(**kwargs)


# --------------------------------------------------------------------------
# Intent routing
# --------------------------------------------------------------------------


async def test_a_plain_turn_captures_nothing() -> None:
    capturer = FakeCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture_for_turn("what did we talk about?", locale="en")

    assert outcome.status == "not_requested"
    assert capturer.grabs == [], "a normal turn must never touch the screen"


async def test_a_desktop_operation_is_left_to_computer_use_without_a_capture() -> None:
    capturer = FakeCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture_for_turn(
        "Klick den Knopf auf meinem Bildschirm",  # i18n-allow: DE input
        locale="de",
    )

    assert outcome.status == "not_requested"
    assert capturer.grabs == []


async def test_an_ambiguous_turn_asks_and_does_not_capture() -> None:
    capturer = FakeCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture_for_turn("was ist das?", locale="de")  # i18n-allow: DE input

    assert outcome.status == "clarify"
    assert outcome.question and outcome.question.endswith("?")
    assert capturer.grabs == [], "ambiguity must never resolve into a capture"


async def test_the_clarifying_question_uses_the_resolved_locale() -> None:
    """The turn's language is passed in, never re-derived here (§1.3)."""
    service = make_service()
    german = await service.capture_for_turn("what is that?", locale="de")
    spanish = await service.capture_for_turn("what is that?", locale="es")
    assert german.question != spanish.question


async def test_an_explicit_request_captures_once() -> None:
    capturer = FakeCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture_for_turn("schau dir das an", locale="de")

    assert outcome.status == "captured"
    assert len(capturer.grabs) == 1, "exactly one capture per trigger"
    assert outcome.context is not None
    assert outcome.context.image, "a captured context must carry image bytes"
    assert outcome.context.mime == "image/jpeg"


async def test_capture_follows_the_cursor_monitor() -> None:
    capturer = FakeCapturer()
    service = make_service(cursor=FakeCursor((-500, 400)), capturer=capturer)

    outcome = await service.capture_for_turn("can you see this?", locale="en")

    assert capturer.grabs[0][0] == (-1920, 0, 1920, 1080)
    assert outcome.context.target.reason is TargetReason.CURSOR_MONITOR


async def test_the_cursor_is_sampled_exactly_once() -> None:
    """Re-reading it later would let a mouse move change which screen is shot."""
    cursor = FakeCursor()
    service = make_service(cursor=cursor)

    await service.capture_for_turn("look at this", locale="en")

    assert cursor.reads == 1


async def test_native_capture_probes_run_off_the_asyncio_thread() -> None:
    loop_thread = threading.get_ident()
    threads: dict[str, list[int]] = {}

    def _record(name: str) -> None:
        threads.setdefault(name, []).append(threading.get_ident())

    class ThreadCursor(FakeCursor):
        def position(self):
            _record("cursor")
            return super().position()

    class ThreadDisplays(FakeDisplays):
        def monitors(self):
            _record("displays")
            return super().monitors()

    class ThreadWindow(FakeWindowProbe):
        def foreground_snapshot(self):
            _record("window")
            return WindowSnapshot(self._facts, self._handle)

    class ThreadCapturer(FakeCapturer):
        def grab(self, bbox, *, window_handle=None):
            _record("capture")
            return super().grab(bbox, window_handle=window_handle)

    def permission_probe():
        _record("permission")
        return None

    service = make_service(
        cursor=ThreadCursor(),
        displays=ThreadDisplays(),
        window_probe=ThreadWindow(),
        capturer=ThreadCapturer(),
        permission_probe=permission_probe,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    assert {"permission", "cursor", "displays", "window", "capture"} <= threads.keys()
    assert all(
        thread_id != loop_thread
        for observed in threads.values()
        for thread_id in observed
    )


async def test_wayland_permission_issue_keeps_its_platform_reason_code() -> None:
    service = make_service(
        permission_probe=lambda: CapturePermissionIssue(
            code="wayland_portal",
            message="A desktop portal is required in this Wayland session.",
        )
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_kind == "technical"
    assert outcome.reason_code == "wayland_portal"


async def test_window_scoped_request_captures_the_window() -> None:
    capturer = FakeCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture_for_turn("look at this window", locale="en")

    assert outcome.context.target.kind is TargetKind.WINDOW
    assert capturer.grabs[0] == ((10, 10, 800, 600), 7)


async def test_window_facts_and_handle_come_from_one_atomic_snapshot() -> None:
    class SnapshotOnlyProbe:
        def foreground_snapshot(self) -> WindowSnapshot:
            return WindowSnapshot(
                facts=WindowFacts(
                    app_name="editor",
                    title="report.pdf",
                    frame_rect=(20, 30, 640, 480),
                ),
                handle=99,
            )

        def foreground(self):
            raise AssertionError("the non-atomic facts path must not run")

        def foreground_handle(self):
            raise AssertionError("the non-atomic handle path must not run")

    capturer = FakeCapturer()
    service = make_service(window_probe=SnapshotOnlyProbe(), capturer=capturer)

    outcome = await service.capture_for_turn("look at this window", locale="en")

    assert outcome.status == "captured"
    assert capturer.grabs[0] == ((20, 30, 640, 480), 99)


# --------------------------------------------------------------------------
# Refusals — the paths where no pixels may exist
# --------------------------------------------------------------------------


async def test_denylisted_window_is_never_captured_at_all() -> None:
    """Not captured and filtered — not captured. The buffer must not exist."""
    capturer = FakeCapturer()
    service = make_service(
        settings=ScreenContextSettings(denylist=("1password",)),
        window_probe=FakeWindowProbe(
            WindowFacts(app_name="1Password.exe", title="Vault", frame_rect=(0, 0, 800, 600))
        ),
        capturer=capturer,
    )

    outcome = await service.capture_for_turn("schau dir das an", locale="de")

    assert outcome.status == "refused"
    assert capturer.grabs == [], "a blocked app must never reach the capturer"
    assert "1password" in (outcome.message or "").lower()


async def test_monitor_capture_checks_every_visible_window_against_denylist() -> None:
    class MultiWindowProbe(FakeWindowProbe):
        def visible_windows(self):
            return (
                WindowFacts(
                    app_name="editor",
                    title="notes",
                    frame_rect=(0, 0, 400, 400),
                ),
                WindowFacts(
                    app_name="vault",
                    title="1Password",
                    frame_rect=(-1500, 0, 400, 400),
                ),
            )

    capturer = FakeCapturer()
    service = make_service(
        settings=ScreenContextSettings(denylist=("1password",)),
        window_probe=MultiWindowProbe(),
        capturer=capturer,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert capturer.grabs == []
    assert "1password" in (outcome.message or "").lower()


async def test_monitor_capture_fails_closed_when_denylist_cannot_be_verified() -> None:
    class UnverifiableProbe(FakeWindowProbe):
        def visible_windows(self):
            return None

    capturer = FakeCapturer()
    service = make_service(
        settings=ScreenContextSettings(denylist=("vault",)),
        window_probe=UnverifiableProbe(),
        capturer=capturer,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert capturer.grabs == []
    assert "could not be verified" in (outcome.message or "")


async def test_missing_permission_refuses_before_capturing() -> None:
    capturer = FakeCapturer()
    service = make_service(
        capturer=capturer,
        permission_probe=lambda: "Screen recording permission is missing.",
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert capturer.grabs == []
    assert "permission" in (outcome.message or "").lower()


async def test_disabled_feature_refuses_honestly() -> None:
    """Switched off must say so, not silently behave like 'no intent'."""
    service = make_service(settings=ScreenContextSettings(enabled=False))
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.status == "refused"
    assert "off" in (outcome.message or "").lower()


async def test_disabled_feature_leaves_ordinary_turns_alone() -> None:
    """The opt-out disables captures, not the rest of the assistant."""
    service = make_service(settings=ScreenContextSettings(enabled=False))
    outcome = await service.capture_for_turn("tell me a joke", locale="en")
    assert outcome.status == "not_requested"


async def test_headless_host_refuses_with_an_explanation() -> None:
    service = make_service(displays=FakeDisplays([]))
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.status == "refused"
    assert outcome.message


async def test_capture_failure_is_reported_not_raised() -> None:
    service = make_service(
        capturer=FakeCapturer(fail=CaptureUnavailable("The display is asleep."))
    )
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.status == "refused"
    assert "asleep" in (outcome.message or "")


async def test_an_unexpected_port_bug_does_not_kill_the_turn() -> None:
    service = make_service(capturer=FakeCapturer(fail=ValueError("boom")))
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.status == "refused"
    assert outcome.reason_kind == "failure"


# --------------------------------------------------------------------------
# Redaction on the real path
# --------------------------------------------------------------------------


async def test_password_field_is_redacted_in_the_delivered_context() -> None:
    reader = FakeTextReader(
        FakeObservation([FakeNode(name="Password", bounds=(-1900, 100, 200, 40), is_password=True)])
    )
    service = make_service(ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    hits = outcome.context.redactions.hits
    assert any(h.rule is RedactionRule.PASSWORD_FIELD for h in hits)
    assert outcome.context.redactions.region_count == 1


async def test_password_node_text_never_enters_the_ui_text() -> None:
    reader = FakeTextReader(
        FakeObservation(
            [
                FakeNode(
                    name="Password",
                    value="hunter2",
                    bounds=(-1900, 100, 200, 40),
                    is_password=True,
                ),
                FakeNode(name="Sign in", bounds=(-1900, 200, 100, 30)),
            ]
        )
    )
    service = make_service(ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert "hunter2" not in outcome.context.ui_text
    assert "Sign in" in outcome.context.ui_text


async def test_sensitive_text_is_scrubbed_from_the_context() -> None:
    reader = FakeTextReader(
        FakeObservation([FakeNode(value="4111 1111 1111 1111", bounds=(-1900, 10, 300, 30))])
    )
    service = make_service(ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert "4111" not in outcome.context.ui_text


async def test_ocr_sensitive_line_is_burned_out_of_delivered_pixels(monkeypatch) -> None:
    from PIL import Image

    from jarvis.screen_context import uitext

    monkeypatch.setattr(
        uitext,
        "ocr_supplement_with_regions",
        lambda _image: uitext.OcrSupplement(
            text="Card 4111 1111 1111 1111",
            regions=(
                uitext.OcrTextRegion(
                    text="Card 4111 1111 1111 1111",
                    bounds=(10, 10, 180, 30),
                ),
            ),
        ),
    )
    service = make_service(settings=ScreenContextSettings(ocr_enabled=True))

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.context.redactions.region_count == 1
    delivered = Image.open(io.BytesIO(outcome.context.image)).convert("RGB")
    assert sum(delivered.getpixel((40, 20))) < 30


async def test_text_from_another_monitor_is_not_included() -> None:
    """The tree spans the desktop; the picture does not."""
    reader = FakeTextReader(
        FakeObservation([FakeNode(name="on the other screen", bounds=(500, 100, 200, 40))])
    )
    service = make_service(cursor=FakeCursor((-500, 400)), ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert "other screen" not in outcome.context.ui_text


async def test_unbounded_text_is_dropped_from_monitor_capture() -> None:
    """An unplaced node may belong to a foreground window on another display."""
    reader = FakeTextReader(
        FakeObservation([FakeNode(name="private text from the other monitor")])
    )
    service = make_service(cursor=FakeCursor((-500, 400)), ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.context.target.kind is TargetKind.MONITOR
    assert outcome.context.ui_text == ""


async def test_unbounded_text_is_retained_for_identity_bound_window_capture() -> None:
    reader = FakeTextReader(FakeObservation([FakeNode(name="window label")]))
    service = make_service(ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this window", locale="en")

    assert outcome.context.target.kind is TargetKind.WINDOW
    assert outcome.context.ui_text == "window label"


async def test_absent_accessibility_layer_is_reported_not_faked() -> None:
    """"We could not read" must never look like "there was no text" (AP-30)."""
    service = make_service(ui_text_reader=FakeTextReader(None))

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.context.ui_text_source == "none"
    assert DegradationCode.NO_UI_TEXT in {d.code for d in outcome.context.degradations}


async def test_accessibility_timeout_degrades_to_image_only(monkeypatch) -> None:
    from jarvis.screen_context import service as service_module

    class HangingTextReader:
        name = "hanging-text"

        async def read(self, *, window_title_filter=None):
            await asyncio.Event().wait()

    monkeypatch.setattr(service_module, "_UI_TEXT_TIMEOUT_S", 0.01)
    service = make_service(ui_text_reader=HangingTextReader())

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    assert outcome.context.ui_text_source == "none"
    assert DegradationCode.NO_UI_TEXT in {
        item.code for item in outcome.context.degradations
    }


async def test_accessibility_text_from_a_different_process_is_discarded() -> None:
    reader = FakeTextReader(
        FakeObservation(
            [FakeNode(name="text from another window")],
            active_pid=99,
            window_title="other.txt",
        )
    )
    service = make_service(ui_text_reader=reader)

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.context.ui_text == ""
    assert outcome.context.redactions.is_empty
    assert DegradationCode.NO_UI_TEXT in {
        item.code for item in outcome.context.degradations
    }


async def test_focus_change_before_shutter_refuses_without_pixels() -> None:
    class SwitchingWindowProbe(FakeWindowProbe):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def foreground_snapshot(self):
            self.reads += 1
            if self.reads == 1:
                return WindowSnapshot(self._facts, self._handle)
            return WindowSnapshot(
                WindowFacts(
                    app_name="mail",
                    title="private message",
                    pid=99,
                    frame_rect=(10, 10, 800, 600),
                ),
                8,
            )

    capturer = FakeCapturer()
    service = make_service(
        window_probe=SwitchingWindowProbe(),
        capturer=capturer,
        ui_text_reader=FakeTextReader(
            FakeObservation([FakeNode(name="captured window text")])
        ),
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_kind == "policy"
    assert capturer.grabs == []


async def test_focus_change_during_shutter_discards_raw_frame() -> None:
    class SwitchingWindowProbe(FakeWindowProbe):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def foreground_snapshot(self):
            self.reads += 1
            if self.reads <= 2:
                return WindowSnapshot(self._facts, self._handle)
            return WindowSnapshot(
                WindowFacts(
                    app_name="vault",
                    title="private vault",
                    pid=99,
                    frame_rect=(10, 10, 800, 600),
                ),
                8,
            )

    capturer = FakeCapturer()
    bus = RecordingBus()
    service = make_service(
        bus=bus, window_probe=SwitchingWindowProbe(), capturer=capturer
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_kind == "policy"
    assert len(capturer.grabs) == 1
    assert service.held_count == 0
    assert [type(event).__name__ for event in bus.events] == [
        "ScreenCaptureAnnounced",
        "ScreenCaptureGrabbed",
        "ScreenCaptureIndicatorDismissed",
    ]


async def test_new_denylisted_monitor_window_during_shutter_discards_frame() -> None:
    class PopupProbe(FakeWindowProbe):
        def __init__(self) -> None:
            super().__init__()
            self.visible_reads = 0

        def visible_windows(self):
            self.visible_reads += 1
            if self.visible_reads < 3:
                return (self._facts,)
            return (
                self._facts,
                WindowFacts(
                    app_name="vault",
                    title="1Password",
                    frame_rect=(-1500, 0, 400, 400),
                ),
            )

    capturer = FakeCapturer()
    service = make_service(
        settings=ScreenContextSettings(denylist=("1password",)),
        window_probe=PopupProbe(),
        capturer=capturer,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_kind == "policy"
    assert len(capturer.grabs) == 1
    assert service.held_count == 0


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


async def test_a_capture_is_consumable_exactly_once() -> None:
    service = make_service()
    outcome = await service.capture_for_turn("look at this", locale="en")

    assert service.consume(outcome.handle_id) is not None
    assert service.consume(outcome.handle_id) is None, "handles are single-use"


async def test_an_unconsumed_capture_expires() -> None:
    now = [1_000_000_000]
    service = make_service(
        settings=ScreenContextSettings(ttl_s=60.0), clock=lambda: now[0]
    )
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert service.peek(outcome.handle_id) is not None

    now[0] += 61 * 1_000_000_000

    assert service.peek(outcome.handle_id) is None
    assert service.held_count == 0


async def test_unconsumed_capture_expires_without_another_service_call() -> None:
    service = make_service(settings=ScreenContextSettings(ttl_s=0.01))
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.handle_id in service._handles

    await asyncio.sleep(0.03)

    assert outcome.handle_id not in service._handles


async def test_closing_service_rejects_new_pixels_and_cancels_timers() -> None:
    service = make_service(settings=ScreenContextSettings(ttl_s=60.0))
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert outcome.handle_id in service._expiry_timers

    assert service.close() == 1
    assert service._handles == {}
    assert service._expiry_timers == {}

    refused = await service.capture_for_turn("look at this", locale="en")
    assert refused.status == "refused"
    assert refused.reason_kind == "technical"


async def test_discard_all_drops_everything_immediately() -> None:
    service = make_service()
    await service.capture_for_turn("look at this", locale="en")
    await service.capture_for_turn("look at this", locale="en")

    assert service.discard_all() == 2
    assert service.held_count == 0


async def test_discard_one_does_not_return_pixels_or_drop_other_handles() -> None:
    service = make_service()
    first = await service.capture_for_turn("look at this", locale="en")
    second = await service.capture_for_turn("look at this", locale="en")

    assert service.discard(first.handle_id) is True
    assert service.discard(first.handle_id) is False
    assert service.peek(second.handle_id) is not None


async def test_nothing_is_written_to_disk(tmp_path, monkeypatch) -> None:
    """The context carries bytes, never a path — a path implies a file."""
    monkeypatch.chdir(tmp_path)
    service = make_service()

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert not any(tmp_path.rglob("*.png")), "no image file may be written"
    assert not any(tmp_path.rglob("*.jpg"))
    assert not hasattr(outcome.context, "screenshot_path")


def test_high_entropy_image_is_always_encoded_below_the_byte_ceiling() -> None:
    from PIL import Image

    random_bytes = secrets.token_bytes(2048 * 2048 * 3)
    image = Image.frombytes("RGB", (2048, 2048), random_bytes)

    encoded, size = _encode(image)

    assert len(encoded) <= 500_000
    assert size[0] < 2048
    assert size[1] < 2048


# --------------------------------------------------------------------------
# Bus / indicator
# --------------------------------------------------------------------------


async def test_the_capture_is_announced_before_the_shutter() -> None:
    """Announcing afterwards leaves a window with no visible sign."""
    bus = RecordingBus()
    capturer = FakeCapturer()
    service = make_service(bus=bus, capturer=capturer)

    await service.capture_for_turn("look at this", locale="en")

    names = [type(e).__name__ for e in bus.events]
    assert names == [
        "ScreenCaptureAnnounced",
        "ScreenCaptureGrabbed",
        "ScreenCaptureCompleted",
        "ScreenCaptureIndicatorDismissed",
    ]


async def test_capture_events_keep_the_turn_trace_id() -> None:
    from uuid import uuid4

    trace_id = uuid4()
    bus = RecordingBus()
    service = make_service(bus=bus)

    await service.capture_for_turn(
        "look at this", locale="en", trace_id=trace_id
    )

    assert [event.trace_id for event in bus.events] == [
        trace_id,
        trace_id,
        trace_id,
        trace_id,
    ]


async def test_no_bus_means_a_reported_limitation_not_a_silent_capture() -> None:
    service = make_service(bus=None)
    outcome = await service.capture_for_turn("look at this", locale="en")
    assert DegradationCode.INDICATOR_UNAVAILABLE in {
        d.code for d in outcome.context.degradations
    }


async def test_the_receipt_event_carries_no_content() -> None:
    """Metadata reaches the flight recorder; pixels and text never do."""
    bus = RecordingBus()
    reader = FakeTextReader(
        FakeObservation([FakeNode(name="secret business plan", bounds=(-1900, 10, 200, 30))])
    )
    service = make_service(bus=bus, ui_text_reader=reader)

    await service.capture_for_turn("look at this", locale="en")

    # The events are slotted dataclasses, so read the declared fields rather
    # than a __dict__ that does not exist.
    import dataclasses

    completed = next(
        event
        for event in bus.events
        if type(event).__name__ == "ScreenCaptureCompleted"
    )
    payload = " ".join(
        str(getattr(completed, f.name)) for f in dataclasses.fields(completed)
    )
    assert "secret business plan" not in payload
    assert completed.bytes_size > 0


async def test_failed_capture_always_dismisses_the_indicator() -> None:
    bus = RecordingBus()
    service = make_service(
        bus=bus,
        capturer=FakeCapturer(fail=CaptureUnavailable("display disappeared")),
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert [type(event).__name__ for event in bus.events] == [
        "ScreenCaptureAnnounced",
        "ScreenCaptureIndicatorDismissed",
    ]


async def test_event_publish_without_indicator_ack_is_not_claimed_as_visible(
    monkeypatch,
) -> None:
    from jarvis.core.bus import EventBus
    from jarvis.screen_context import service as service_module

    monkeypatch.setattr(service_module, "_ANNOUNCE_TIMEOUT_S", 0.01)
    service = make_service(bus=EventBus())

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert DegradationCode.INDICATOR_UNAVAILABLE in {
        item.code for item in outcome.context.degradations
    }


# --------------------------------------------------------------------------
# Forced capture (REST / a bar button)
# --------------------------------------------------------------------------


async def test_force_skips_classification_but_not_privacy() -> None:
    capturer = FakeCapturer()
    service = make_service(
        settings=ScreenContextSettings(denylist=("vault",)),
        window_probe=FakeWindowProbe(
            WindowFacts(app_name="pw", title="My Vault", frame_rect=(0, 0, 800, 600))
        ),
        capturer=capturer,
    )

    outcome = await service.capture_for_turn("", locale="en", force=True)

    assert outcome.status == "refused"
    assert capturer.grabs == [], "force must never bypass the denylist"


async def test_force_captures_without_any_utterance() -> None:
    service = make_service()
    outcome = await service.capture_for_turn("", locale="en", force=True)
    assert outcome.status == "captured"


# --------------------------------------------------------------------------
# Config bridge
# --------------------------------------------------------------------------


def test_settings_from_a_config_without_the_block_are_defaults() -> None:
    """An older jarvis.toml has no [screen_context]; that must not explode."""

    class OldConfig:
        pass

    settings = settings_from_config(OldConfig())
    assert settings.enabled is True
    assert settings.denylist == ()


def test_settings_are_read_from_the_config_block() -> None:
    from jarvis.core.config import JarvisConfig

    cfg = JarvisConfig()
    cfg.screen_context.denylist = ["1password"]
    cfg.screen_context.ttl_s = 30.0
    cfg.screen_context.ocr_enabled = True

    settings = settings_from_config(cfg)

    assert settings.denylist == ("1password",)
    assert settings.ttl_s == 30.0
    assert settings.ocr_enabled is True


def test_describe_names_what_was_captured() -> None:
    """The receipt is what the user is told; it must be concrete."""
    from jarvis.screen_context.models import CaptureTarget, ScreenContext

    context = ScreenContext(
        image=b"x",
        mime="image/jpeg",
        size=(1920, 1080),
        target=CaptureTarget(
            kind=TargetKind.MONITOR,
            bbox=(0, 0, 1920, 1080),
            reason=TargetReason.CURSOR_MONITOR,
            monitor_name="2",
        ),
    )

    described = context.describe()
    assert "monitor 2" in described
    assert "1920x1080" in described
