"""Turn integration — the contract a conversation layer relies on.

The refused/unavailable split gets the most attention here. Collapsing it fails
in one of two ways, and both are the kind that ship quietly:

* treating a privacy refusal as technical lets the caller fall back to another
  screen path and photograph the protected window;
* treating a missing display as a prohibition ends every look-request on a
  headless host with a refusal instead of a normal answer.
"""
from __future__ import annotations

from jarvis.screen_context.models import (
    CaptureTarget,
    Degradation,
    DegradationCode,
    IntentVerdict,
    RedactionHit,
    RedactionReport,
    RedactionRule,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.service import CaptureOutcome
from jarvis.screen_context.turn import screen_context_for_turn


class FakeService:
    """Returns a scripted outcome and records what it was consumed for."""

    def __init__(self, outcome: CaptureOutcome) -> None:
        self._outcome = outcome
        self.consumed: list[str] = []

    async def capture_for_turn(self, text, *, locale="", force=False):
        return self._outcome

    def consume(self, handle_id):
        self.consumed.append(handle_id)
        return None


def make_context(**overrides) -> ScreenContext:
    defaults = {
        "image": b"jpeg-bytes",
        "mime": "image/jpeg",
        "size": (1920, 1080),
        "target": CaptureTarget(
            kind=TargetKind.MONITOR,
            bbox=(0, 0, 1920, 1080),
            reason=TargetReason.CURSOR_MONITOR,
            monitor_name="2",
            window=WindowFacts(app_name="editor", title="notes.md"),
        ),
        "ui_text": "Build failed: 3 errors",
        "ui_text_source": "accessibility",
        "captured_at_ns": 123,
    }
    defaults.update(overrides)
    return ScreenContext(**defaults)


def outcome(status, **kwargs) -> CaptureOutcome:
    return CaptureOutcome(
        status=status, verdict=IntentVerdict(intent=VisualIntent.SCREEN), **kwargs
    )


async def test_no_visual_intent_leaves_the_turn_alone() -> None:
    service = FakeService(outcome("not_requested"))
    result = await screen_context_for_turn("hello", locale="en", service=service)

    assert result.status == "none"
    assert not result.ends_the_turn
    assert not result.blocks_other_screen_paths


async def test_ambiguous_ends_the_turn_and_shuts_other_paths() -> None:
    service = FakeService(outcome("clarify", question="Shall I look?"))
    result = await screen_context_for_turn("what is that?", locale="en", service=service)

    assert result.status == "clarify"
    assert result.question == "Shall I look?"
    assert result.ends_the_turn
    assert result.blocks_other_screen_paths, (
        "asking whether to look while another path attaches an image is the "
        "one outcome this must never allow"
    )


async def test_privacy_refusal_ends_the_turn_and_shuts_other_paths() -> None:
    service = FakeService(
        outcome("refused", reason_kind="policy", message="Your privacy rule blocked it.")
    )
    result = await screen_context_for_turn("look at this", locale="en", service=service)

    assert result.status == "refused"
    assert result.ends_the_turn
    assert result.blocks_other_screen_paths, (
        "falling back here would capture the very window the rule protects"
    )


async def test_technical_failure_lets_the_turn_continue() -> None:
    """A headless host must answer normally, not refuse every look-request."""
    service = FakeService(
        outcome("refused", reason_kind="technical", message="No display available.")
    )
    result = await screen_context_for_turn("look at this", locale="en", service=service)

    assert result.status == "unavailable"
    assert not result.ends_the_turn
    assert not result.blocks_other_screen_paths
    assert result.message, "the reason must survive for the log"


async def test_captured_carries_image_and_note() -> None:
    service = FakeService(
        outcome("captured", context=make_context(), handle_id="abc123")
    )
    result = await screen_context_for_turn("look at this", locale="en", service=service)

    assert result.status == "captured"
    assert result.has_image
    assert result.image == b"jpeg-bytes"
    assert result.mime == "image/jpeg"
    assert "monitor 2" in result.note
    assert "editor" in result.note
    assert "Build failed" in result.note


async def test_the_capture_is_consumed_by_the_turn() -> None:
    """This turn IS the single use; parking it for the TTL helps nobody."""
    service = FakeService(
        outcome("captured", context=make_context(), handle_id="abc123")
    )
    await screen_context_for_turn("look at this", locale="en", service=service)

    assert service.consumed == ["abc123"]


async def test_redactions_are_declared_to_the_model() -> None:
    """Unexplained black boxes get narrated as user interface."""
    context = make_context(
        redactions=RedactionReport(
            hits=(
                RedactionHit(
                    rule=RedactionRule.PASSWORD_FIELD,
                    label="password_field",
                    region=(0, 0, 10, 10),
                ),
            )
        )
    )
    service = FakeService(outcome("captured", context=context, handle_id="h"))

    result = await screen_context_for_turn("look at this", locale="en", service=service)

    assert "withheld" in result.note.lower()
    assert "do not describe" in result.note.lower()


async def test_degradations_are_declared_to_the_model() -> None:
    context = make_context(
        degradations=(
            Degradation(
                code=DegradationCode.NO_UI_TEXT,
                message="On-screen text could not be read on this system.",
            ),
        )
    )
    service = FakeService(outcome("captured", context=context, handle_id="h"))

    result = await screen_context_for_turn("look at this", locale="en", service=service)

    assert "Limitations" in result.note
    assert "could not be read" in result.note


async def test_window_scope_names_the_window_not_the_monitor() -> None:
    context = make_context(
        target=CaptureTarget(
            kind=TargetKind.WINDOW,
            bbox=(0, 0, 800, 600),
            reason=TargetReason.FOCUSED_WINDOW,
            monitor_name="1",
            window=WindowFacts(app_name="editor", title="report.pdf"),
        )
    )
    service = FakeService(outcome("captured", context=context, handle_id="h"))

    result = await screen_context_for_turn("look at this window", locale="en", service=service)

    assert "report.pdf" in result.note


async def test_a_broken_service_never_breaks_the_turn() -> None:
    """A bug in Screen Context must degrade to 'no screen context', not raise."""

    class ExplodingService:
        async def capture_for_turn(self, text, *, locale="", force=False):
            raise RuntimeError("boom")

    result = await screen_context_for_turn(
        "look at this", locale="en", service=ExplodingService()
    )

    assert result.status == "none"
    assert not result.ends_the_turn
