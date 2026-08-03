"""``ScreenContextService`` — one capture, on request, then gone.

This is the orchestration layer: it asks the classifier whether the user
actually asked to look, checks permissions, picks the surface, announces the
capture, grabs pixels exactly once, redacts them, and hands back a short-lived
handle. Every piece of platform knowledge lives below it in ``ports.py``, so
this module is fully unit-testable with fakes and needs no display.

**Single capture, by construction.** There is no capture loop, cached frame, or
"refresh". :meth:`ScreenContextService.capture` grabs once and returns; the
only way to get another capture is another explicit call. The only timer is a
destructive TTL callback for an unconsumed handle.

**Retention.** A finished :class:`~jarvis.screen_context.models.ScreenContext`
lives in a :class:`_Handle` in memory, keyed by an opaque id, and is removed on
the first :meth:`consume` or when its TTL expires, whichever comes first.
Nothing is written to disk. This is a deliberate departure from
``jarvis.vision.screenshot.ScreenshotSource``, which persists every frame to
``data/flight_recorder/blobs/`` — correct for Computer-Use replay, wrong for a
feature whose promise is that the picture does not stick around. That is also
why this module uses the stateless ``capture_region``-style grab through the
port rather than reusing ``ScreenshotSource``.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from jarvis.screen_context import intent as intent_module
from jarvis.screen_context import redaction, uitext
from jarvis.screen_context.models import (
    CaptureTarget,
    Degradation,
    DegradationCode,
    IntentVerdict,
    ScreenContext,
    TargetKind,
    VisualIntent,
)
from jarvis.screen_context.ports import (
    CapturePermissionIssue,
    CaptureUnavailable,
    accessibility_permission_error,
    capture_permission_error,
    make_bar_locator,
    make_cursor_locator,
    make_display_enumerator,
    make_surface_capturer,
    make_ui_text_reader,
    make_window_probe,
)
from jarvis.screen_context.targeting import resolve_target

log = logging.getLogger(__name__)

#: Vision models downscale to roughly this on the long edge for token
#: accounting, so anything larger costs bytes and latency for no added detail.
#: Same value the existing screenshot tool settled on.
_MAX_DIMENSION = 2048
_JPEG_QUALITY = 85
_MAX_IMAGE_BYTES = 500_000
_MIN_JPEG_QUALITY = 50

#: How long the announcement gets to reach a subscriber before the shutter.
#: Short by design: a hung subscriber must not stall a voice turn, and the
#: degradation is recorded rather than the capture being abandoned.
_ANNOUNCE_TIMEOUT_S = 1.5
_INDICATOR_DWELL_S = 0.12
_UI_TEXT_TIMEOUT_S = 2.0

CaptureStatus = Literal["captured", "clarify", "refused", "not_requested"]


@dataclass(frozen=True, slots=True)
class ScreenContextSettings:
    """Everything configurable, resolved once and passed in.

    A plain dataclass rather than a direct ``JarvisConfig`` read so the service
    stays testable without a config file, and so every setting the service
    honours is visible in one place (AP-31: no key that nothing reads).
    """

    enabled: bool = True
    #: App names / window-title fragments that are never captured at all.
    denylist: tuple[str, ...] = ()
    #: Extra ``"label:regex"`` patterns on top of the shipped defaults.
    extra_patterns: tuple[str, ...] = ()
    include_default_patterns: bool = True
    #: Character budget for on-screen text handed to the model.
    max_text_chars: int = 4000
    #: Seconds an unconsumed capture stays in memory.
    ttl_s: float = 120.0
    #: OCR is a supplement, off unless the user asks for it.
    ocr_enabled: bool = False
    #: ``[computer_use].main_monitor``-style override for the last-resort pick.
    main_monitor: str = "primary"


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """What happened, in a shape the caller can act on without guessing.

    Four statuses, and the caller must handle all four:

    * ``not_requested`` — the turn contained no visual intent. Carry on.
    * ``clarify`` — ambiguous. Ask ``question`` and capture nothing.
    * ``refused`` — capture was not possible or not allowed. Say ``message``.
    * ``captured`` — ``handle_id`` and ``context`` are set.

    A refusal additionally carries ``reason_kind``, and the difference matters
    more than it looks:

    * ``policy`` — the user's own rules said no (denylist, feature off). A
      caller must NOT quietly fall back to some other way of seeing the screen;
      that would photograph the very window the rule protects.
    * ``technical`` — this machine could not (no display, no permission). A
      caller should expose the structured remediation and must not turn a pure
      observation into Computer-Use as an implicit fallback.
    * ``failure`` — an unexpected implementation/backend defect. Alternate
      screen paths stay closed so they cannot retry without this policy layer.
    """

    status: CaptureStatus
    verdict: IntentVerdict
    context: ScreenContext | None = None
    handle_id: str | None = None
    question: str | None = None
    message: str | None = None
    reason_kind: Literal["", "policy", "technical", "failure"] = ""
    reason_code: Literal[
        "",
        "capture_permission",
        "wayland_portal",
        "no_display",
        "capture_backend_unavailable",
    ] = ""


@dataclass
class _Handle:
    context: ScreenContext
    expires_at_ns: int


class ScreenContextService:
    """One-shot screen context for the on-screen bar and the voice session."""

    def __init__(
        self,
        *,
        settings: ScreenContextSettings | None = None,
        bus: Any | None = None,
        cursor: Any | None = None,
        bar: Any | None = None,
        displays: Any | None = None,
        window_probe: Any | None = None,
        capturer: Any | None = None,
        ui_text_reader: Any | None = None,
        permission_probe: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self._settings = settings or ScreenContextSettings()
        self._bus = None
        if bus is not None:
            self.bind_bus(bus)
        # Ports are constructed lazily on first use, never at import or at
        # boot (AP-26): building a cursor backend or an accessibility source
        # costs native library loads that must not sit on the startup path.
        self._cursor = cursor
        self._bar = bar
        self._displays = displays
        self._window_probe = window_probe
        self._capturer = capturer
        self._ui_text_reader = ui_text_reader
        self._permission_probe = permission_probe or capture_permission_error
        self._clock = clock or time.monotonic_ns
        self._wall_clock = time.time_ns
        self._handles: dict[str, _Handle] = {}
        self._expiry_timers: dict[str, asyncio.TimerHandle | threading.Timer] = {}
        self._handle_lock = threading.RLock()
        self._closed = False
        self._patterns = redaction.build_patterns(
            self._settings.extra_patterns,
            include_defaults=self._settings.include_default_patterns,
        )

    @property
    def settings(self) -> ScreenContextSettings:
        """The resolved settings this service is running with (read-only)."""
        return self._settings

    # ---- ports (lazy) ----------------------------------------------------

    @property
    def cursor(self):
        if self._cursor is None:
            self._cursor = make_cursor_locator()
        return self._cursor

    @property
    def bar(self):
        if self._bar is None:
            self._bar = make_bar_locator()
        return self._bar

    @property
    def displays(self):
        if self._displays is None:
            self._displays = make_display_enumerator()
        return self._displays

    @property
    def window_probe(self):
        if self._window_probe is None:
            self._window_probe = make_window_probe()
        return self._window_probe

    @property
    def capturer(self):
        if self._capturer is None:
            self._capturer = make_surface_capturer()
        return self._capturer

    @property
    def ui_text_reader(self):
        if self._ui_text_reader is None:
            self._ui_text_reader = make_ui_text_reader()
        return self._ui_text_reader

    def bind_bus(self, bus: Any) -> None:
        """Attach receipts and the lazy process-wide sound-effect service."""
        if self._bus is None:
            self._bus = bus
        elif self._bus is not bus:
            log.warning(
                "screen_context: ignored an attempt to bind a second EventBus"
            )
            return
        if hasattr(bus, "subscribe"):
            from jarvis.audio.effects import attach_audio_effects  # noqa: PLC0415

            attach_audio_effects(bus)

    # ---- intent ----------------------------------------------------------

    def classify(self, text: str, *, locale: str = "") -> IntentVerdict:
        """Public wrapper so callers never import the matcher directly."""
        return intent_module.classify(text, locale=locale)

    # ---- the capture -----------------------------------------------------

    async def capture_for_turn(
        self,
        text: str,
        *,
        locale: str = "",
        force: bool = False,
        trace_id: UUID | None = None,
    ) -> CaptureOutcome:
        """Classify one user turn and capture only if it unambiguously asked.

        ``locale`` must be the ALREADY-RESOLVED output language for this turn
        (``jarvis.core.turn_language.resolve_output_language``). This service
        never derives a language itself — a second derivation is exactly the
        mid-session language flip CLAUDE.md §1.3 forbids.

        ``force`` skips classification for callers that are not a conversation
        turn (the REST endpoint, an explicit bar button). It never skips
        permissions, the denylist, or redaction.
        """
        verdict = (
            IntentVerdict(intent=VisualIntent.SCREEN, evidence=("forced",), locale=locale)
            if force
            else self.classify(text, locale=locale)
        )

        # A disabled feature is a policy refusal only when the user actually
        # asked for it. Ordinary conversation must remain a no-op; checking the
        # switch first would make disabling Screen Context block every turn.
        if verdict.intent is VisualIntent.NONE:
            return CaptureOutcome(status="not_requested", verdict=verdict)

        if not self._settings.enabled:
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="policy",
                message=(
                    "Screen context is switched off. You can turn it back on in "
                    "Settings."
                ),
            )

        if verdict.intent is VisualIntent.AMBIGUOUS:
            log.info(
                "screen_context: ambiguous visual intent (evidence=%s) — asking "
                "instead of capturing",
                verdict.evidence,
            )
            return CaptureOutcome(
                status="clarify",
                verdict=verdict,
                question=intent_module.clarifying_question(locale),
            )

        return await self.capture(verdict=verdict, trace_id=trace_id)

    async def capture(
        self,
        *,
        verdict: IntentVerdict | None = None,
        trace_id: UUID | None = None,
    ) -> CaptureOutcome:
        """Take exactly one capture. Assumes intent is already established."""
        verdict = verdict or IntentVerdict(intent=VisualIntent.SCREEN)
        if self._closed:
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="technical",
                reason_code="capture_backend_unavailable",
                message="Screen Context settings changed before capture could start.",
            )

        permission_issue = await asyncio.to_thread(self._permission_probe)
        if permission_issue:
            if isinstance(permission_issue, CapturePermissionIssue):
                reason_code = permission_issue.code
                permission_message = permission_issue.message
            else:
                # Compatibility for injected third-party/test probes that still
                # implement the original ``str | None`` port contract.
                reason_code = "capture_permission"
                permission_message = str(permission_issue)
            log.info("screen_context: capture refused — %s", permission_message)
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="technical",
                reason_code=reason_code,
                message=permission_message,
            )

        # The cursor is sampled ONCE, here, and threaded through. See
        # targeting.resolve_target for why re-reading it later is a race.
        cursor_point = await asyncio.to_thread(self.cursor.position)
        bar_point = (
            await asyncio.to_thread(self.bar.position)
            if cursor_point is None
            else None
        )
        # Facts and native handle must describe the SAME foreground window.
        # Reading them separately lets a focus switch route a denylist verdict
        # for window A into a native capture of window B (especially on macOS).
        window_handle = None
        window_probe = self.window_probe
        snapshot_getter = getattr(window_probe, "foreground_snapshot", None)
        snapshot = (
            await asyncio.to_thread(snapshot_getter)
            if callable(snapshot_getter)
            else None
        )
        if snapshot is not None:
            window_facts = snapshot.facts
            window_handle = snapshot.handle
        else:
            # Compatibility seam for injected test/platform probes. Production
            # PlatformWindowProbe always provides the atomic snapshot method.
            window_facts = await asyncio.to_thread(window_probe.foreground)

        degradations: list[Degradation] = []
        if window_facts is None:
            degradations.append(
                Degradation(
                    code=DegradationCode.NO_WINDOW_FACTS,
                    message=(
                        "The active application could not be identified, so the "
                        "screen was captured without app or window information."
                    ),
                )
            )

        # Denylist BEFORE targeting and before any pixels exist.
        if window_facts is not None:
            if self._settings.denylist and not window_facts.app_name:
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=(
                        "I did not capture the screen because the active "
                        "application could not be identified for your privacy "
                        "denylist."
                    ),
                )
            blocked_by = redaction.blocked_by_denylist(
                window_facts, self._settings.denylist
            )
            if blocked_by:
                log.info(
                    "screen_context: capture blocked by denylist entry %r", blocked_by
                )
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=(
                        f"I did not capture the screen: the active window matches "
                        f"your privacy rule '{blocked_by}', so screen context is "
                        "switched off for it."
                    ),
                    context=None,
                )

        if verdict.intent is VisualIntent.WINDOW and snapshot is None:
            getter = getattr(window_probe, "foreground_handle", None)
            if callable(getter):
                window_handle = await asyncio.to_thread(getter)

        try:
            target, target_degradations = resolve_target(
                verdict.intent,
                monitors=await asyncio.to_thread(self.displays.monitors),
                cursor_point=cursor_point,
                bar_point=bar_point,
                window=window_facts,
                window_handle=window_handle,
                main_monitor_override=self._settings.main_monitor,
            )
        except CaptureUnavailable as exc:
            log.info("screen_context: no capture target — %s", exc)
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="technical",
                reason_code="no_display",
                message=str(exc),
            )
        degradations.extend(target_degradations)

        # A monitor capture contains more than the foreground window. When the
        # user configured a denylist, verify every visible surface intersecting
        # that monitor before any pixels exist. Unknown window geometry counts
        # as intersecting (fail closed), and an unavailable enumeration blocks
        # monitor scope rather than silently weakening the privacy rule.
        monitor_privacy_error = await asyncio.to_thread(
            self._monitor_privacy_error, target
        )
        if monitor_privacy_error:
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="policy",
                message=monitor_privacy_error,
            )

        # Announce BEFORE the shutter so the indicator is up while there is
        # still something to indicate.
        event_trace_id = trace_id or uuid4()
        announced = await self._announce(target, trace_id=event_trace_id)
        if not announced:
            degradations.append(
                Degradation(
                    code=DegradationCode.INDICATOR_UNAVAILABLE,
                    message=(
                        "The capture indicator could not be shown, so this "
                        "capture happened without an on-screen signal."
                    ),
                )
            )
        else:
            # Give the composited border a perceptible pre-shutter moment. The
            # renderer ACK proves it was processed; this short dwell makes the
            # privacy signal human-visible rather than a one-frame flicker.
            await asyncio.sleep(_INDICATOR_DWELL_S)

        try:
            # Recheck at the shutter boundary. A password manager can gain
            # focus or open on the selected monitor while the indicator is
            # appearing; the earlier policy decision must not authorize that
            # newly visible surface.
            if target.window.is_known and not await asyncio.to_thread(
                self._foreground_still_matches,
                target.window,
                expected_window_handle=window_handle,
            ):
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=(
                        "I did not capture the screen because the active window "
                        "changed while the capture was being prepared. Ask again "
                        "when the intended window is in front."
                    ),
                )
            monitor_privacy_error = await asyncio.to_thread(
                self._monitor_privacy_error, target
            )
            if monitor_privacy_error:
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=monitor_privacy_error,
                )
            try:
                size, rgb = await asyncio.to_thread(
                    self.capturer.grab,
                    target.bbox,
                    window_handle=target.window_handle,
                )
            except CaptureUnavailable as exc:
                log.info("screen_context: capture failed — %s", exc)
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="technical",
                    reason_code="capture_backend_unavailable",
                    message=str(exc),
                )
            except Exception:  # noqa: BLE001 - port bugs stay turn-local
                log.error("screen_context: unexpected capture failure", exc_info=True)
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="failure",
                    message=(
                        "The screen could not be captured because the capture "
                        "backend failed."
                    ),
                )

            # This is the shutter boundary: pixels exist now. Publish only
            # metadata so the shared audio layer can play its cue at the
            # truthful moment without receiving or retaining screen content.
            await self._publish_grabbed(size, trace_id=event_trace_id)

            # Treat a post-shutter identity change as untrusted: discard the
            # raw bytes before redaction/storage rather than attaching pixels
            # from a surface different from the one that passed policy.
            if target.window.is_known and not await asyncio.to_thread(
                self._foreground_still_matches,
                target.window,
                expected_window_handle=window_handle,
            ):
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=(
                        "I discarded the screenshot because the active window "
                        "changed during capture. Ask again when the intended "
                        "window is stable."
                    ),
                )
            monitor_privacy_error = await asyncio.to_thread(
                self._monitor_privacy_error, target
            )
            if monitor_privacy_error:
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="policy",
                    message=monitor_privacy_error,
                )

            context = await self._build_context(
                target=target,
                raw_size=size,
                rgb=rgb,
                degradations=degradations,
                expected_window_handle=window_handle,
            )

            if self._closed:
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="technical",
                    reason_code="capture_backend_unavailable",
                    message=(
                        "Screen Context settings changed during capture, so the "
                        "image was discarded. Ask again to use the new settings."
                    ),
                )
            try:
                handle_id = self.store(context)
            except RuntimeError:
                return CaptureOutcome(
                    status="refused",
                    verdict=verdict,
                    reason_kind="technical",
                    reason_code="capture_backend_unavailable",
                    message=(
                        "Screen Context settings changed during capture, so the "
                        "image was discarded. Ask again to use the new settings."
                    ),
                )
            await self._publish_completed(context, trace_id=event_trace_id)
            log.info("screen_context: %s", context.describe())
            return CaptureOutcome(
                status="captured",
                verdict=verdict,
                context=context,
                handle_id=handle_id,
            )
        finally:
            await self._dismiss_indicator(trace_id=event_trace_id)

    # ---- assembly --------------------------------------------------------

    def _monitor_privacy_error(self, target: CaptureTarget) -> str | None:
        """Return a refusal when monitor-wide denylist safety is unverifiable."""
        if target.kind is not TargetKind.MONITOR or not self._settings.denylist:
            return None
        visible_getter = getattr(self.window_probe, "visible_windows", None)
        visible = visible_getter() if callable(visible_getter) else None
        if visible is None:
            return (
                "I did not capture the monitor because its visible windows "
                "could not be verified against your privacy denylist. Ask for "
                "the active window instead."
            )
        for candidate in visible:
            if not candidate.app_name:
                return (
                    "I did not capture the monitor because a visible "
                    "application could not be identified for your privacy "
                    "denylist. Ask for the active window instead."
                )
            blocked_by = redaction.blocked_by_denylist(
                candidate, self._settings.denylist
            )
            if blocked_by and _rects_intersect_or_unknown(
                candidate.frame_rect, target.bbox
            ):
                return (
                    "I did not capture the monitor because a visible window "
                    f"matches your privacy rule '{blocked_by}'."
                )
        return None

    async def _build_context(
        self,
        *,
        target: CaptureTarget,
        raw_size: tuple[int, int],
        rgb: bytes,
        degradations: list[Degradation],
        expected_window_handle: int | None,
    ) -> ScreenContext:
        """Read text, redact pixels and text, encode. In that order.

        Order is load-bearing: redaction happens on the RAW frame, before any
        encoding or downscaling, so the black boxes are burned into the pixels
        rather than layered over bytes that still hold the original.
        """
        from PIL import Image  # noqa: PLC0415

        image = Image.frombytes("RGB", raw_size, rgb)

        # -- on-screen text -------------------------------------------------
        text, text_source, nodes, text_degradations = "", "none", (), ()
        access_error = await asyncio.to_thread(accessibility_permission_error)
        if access_error:
            degradations.append(
                Degradation(code=DegradationCode.NO_UI_TEXT, message=access_error)
            )
        else:
            try:
                text, text_source, nodes, text_degradations = await asyncio.wait_for(
                    uitext.read_ui_text(
                        self.ui_text_reader,
                        target_rect=target.bbox,
                        max_chars=self._settings.max_text_chars,
                        keep_unbounded_nodes=target.kind is TargetKind.WINDOW,
                        window_title_filter=(
                            target.window.title
                            if target.kind is TargetKind.WINDOW
                            else None
                        ),
                        expected_pid=target.window.pid,
                        expected_window_title=target.window.title,
                    ),
                    timeout=_UI_TEXT_TIMEOUT_S,
                )
            except TimeoutError:
                text, text_source, nodes, text_degradations = "", "none", (), ()
                degradations.append(
                    Degradation(
                        code=DegradationCode.NO_UI_TEXT,
                        message=(
                            "On-screen text took too long to read, so this turn "
                            "continued with the image only."
                        ),
                    )
                )
                log.warning(
                    "screen_context: accessibility read exceeded %.1fs; "
                    "continuing image-only",
                    _UI_TEXT_TIMEOUT_S,
                )
            degradations.extend(text_degradations)
            if nodes and not await asyncio.to_thread(
                self._foreground_still_matches,
                target.window,
                expected_window_handle=expected_window_handle,
            ):
                text, text_source, nodes = "", "none", ()
                degradations.append(
                    Degradation(
                        code=DegradationCode.NO_UI_TEXT,
                        message=(
                            "The active window changed after the screenshot, so "
                            "its accessibility text was discarded instead of "
                            "being attached to the earlier image."
                        ),
                    )
                )

        # -- image redaction, on the raw frame ------------------------------
        # macOS returns backing pixels for a rect measured in points, so the
        # capture can be 2x the geometry it was asked for. Deriving the scale
        # from the actual image keeps the black boxes on top of what they are
        # meant to cover instead of a quarter of it.
        scale = (raw_size[0] / target.width) if target.width else 1.0
        regions = redaction.regions_to_redact(
            nodes, target_bbox=target.bbox, patterns=self._patterns, scale=scale
        )
        image, region_hits = redaction.apply_image_redactions(image, regions)

        # -- text scrubbing --------------------------------------------------
        scrubbed_text, text_hits = redaction.scrub_text(text, self._patterns)

        # -- OCR -------------------------------------------------------------
        # When enabled, OCR always runs once so matches can be burned out of
        # pixels even when accessibility already returned enough model text.
        # OCR text itself supplements only genuinely sparse accessibility.
        ocr_region_hits = ()
        if self._settings.ocr_enabled:
            ocr_result = await asyncio.to_thread(
                uitext.ocr_supplement_with_regions,
                image,
            )
            if ocr_result.degradation is not None:
                degradations.append(ocr_result.degradation)
            else:
                ocr_regions = redaction.local_text_regions_to_redact(
                    ocr_result.regions,
                    patterns=self._patterns,
                )
                image, ocr_region_hits = redaction.apply_image_redactions(
                    image,
                    ocr_regions,
                )
                if ocr_result.text and uitext.text_is_sparse(
                    scrubbed_text,
                    image_size=raw_size,
                ):
                    ocr_scrubbed, ocr_hits = redaction.scrub_text(
                        ocr_result.text,
                        self._patterns,
                    )
                    scrubbed_text = (
                        f"{scrubbed_text}\n{ocr_scrubbed}".strip()
                        if scrubbed_text
                        else ocr_scrubbed
                    )
                    text_hits = text_hits + ocr_hits
                    text_source = (
                        "ocr" if text_source == "none" else "accessibility+ocr"
                    )

        image_bytes, encoded_size = _encode(image)

        return ScreenContext(
            image=image_bytes,
            mime="image/jpeg",
            size=encoded_size,
            target=target,
            ui_text=scrubbed_text,
            ui_text_source=text_source,
            redactions=redaction.merge_reports(
                region_hits,
                ocr_region_hits,
                text_hits,
            ),
            degradations=tuple(degradations),
            captured_at_ns=self._wall_clock(),
        )

    def _foreground_still_matches(
        self,
        expected: Any,
        *,
        expected_window_handle: int | None,
    ) -> bool:
        """Recheck focus after accessibility traversal to close its race."""
        snapshot_getter = getattr(self.window_probe, "foreground_snapshot", None)
        if not callable(snapshot_getter):
            return True  # injected legacy/test seam; production always supports it
        current = snapshot_getter()
        if current is None:
            return False

        comparisons: list[bool] = []
        if expected_window_handle is not None and current.handle is not None:
            comparisons.append(int(expected_window_handle) == int(current.handle))
        if expected.pid and current.facts.pid:
            comparisons.append(int(expected.pid) == int(current.facts.pid))
        expected_title = " ".join(str(expected.title or "").casefold().split())
        current_title = " ".join(
            str(current.facts.title or "").casefold().split()
        )
        if expected_title and current_title:
            comparisons.append(expected_title == current_title)
        return all(comparisons) if comparisons else not expected.is_known

    # ---- handles ---------------------------------------------------------

    def store(self, context: ScreenContext) -> str:
        """Park a context behind an opaque, single-use id."""
        with self._handle_lock:
            if self._closed:
                raise RuntimeError("Screen Context service is closed")
            self.sweep()
            handle_id = secrets.token_urlsafe(12)
            expires_at_ns = self._clock() + int(
                self._settings.ttl_s * 1_000_000_000
            )
            self._handles[handle_id] = _Handle(
                context=context,
                expires_at_ns=expires_at_ns,
            )
            self._schedule_expiry(handle_id, expires_at_ns)
            return handle_id

    def consume(self, handle_id: str) -> ScreenContext | None:
        """Take a context out. A second call for the same id gets ``None``.

        Single consumption is the retention promise in code: the caller cannot
        hold an id and re-fetch the picture later, and nothing has to remember
        to clean up.
        """
        with self._handle_lock:
            self.sweep()
            handle = self._handles.pop(handle_id, None)
            self._cancel_expiry(handle_id)
            return handle.context if handle is not None else None

    def peek(self, handle_id: str) -> ScreenContext | None:
        """Metadata read WITHOUT consuming — used by the receipt endpoint."""
        with self._handle_lock:
            self.sweep()
            handle = self._handles.get(handle_id)
            return handle.context if handle is not None else None

    def discard(self, handle_id: str) -> bool:
        """Remove one held capture without returning its pixels."""
        with self._handle_lock:
            removed = self._handles.pop(handle_id, None) is not None
            self._cancel_expiry(handle_id)
            return removed

    def sweep(self) -> int:
        """Drop expired handles. Returns how many were dropped."""
        with self._handle_lock:
            now = self._clock()
            expired = [
                key
                for key, handle in self._handles.items()
                if handle.expires_at_ns <= now
            ]
            for key in expired:
                self._handles.pop(key, None)
                self._cancel_expiry(key)
        if expired:
            log.debug("screen_context: dropped %d expired capture(s)", len(expired))
        return len(expired)

    def _schedule_expiry(
        self,
        handle_id: str,
        expires_at_ns: int,
        *,
        delay_s: float | None = None,
    ) -> None:
        """Own the TTL lifecycle instead of waiting for another API call."""
        delay_s = max(
            0.0,
            self._settings.ttl_s if delay_s is None else delay_s,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # Non-async callers use the equivalent timer fallback.
            timer = threading.Timer(
                delay_s,
                self._expire_handle,
                args=(handle_id, expires_at_ns),
            )
            timer.daemon = True
            self._expiry_timers[handle_id] = timer
            timer.start()
        else:
            self._expiry_timers[handle_id] = loop.call_later(
                delay_s,
                self._expire_handle,
                handle_id,
                expires_at_ns,
            )

    def _expire_handle(self, handle_id: str, expires_at_ns: int) -> None:
        with self._handle_lock:
            handle = self._handles.get(handle_id)
            if handle is None or handle.expires_at_ns != expires_at_ns:
                return
            if handle.expires_at_ns > self._clock():
                # A fake or adjusted clock has not reached the deadline yet.
                remaining_s = (handle.expires_at_ns - self._clock()) / 1_000_000_000
                self._schedule_expiry(
                    handle_id,
                    expires_at_ns,
                    delay_s=remaining_s,
                )
                return
            self._handles.pop(handle_id, None)
            self._expiry_timers.pop(handle_id, None)
        log.debug("screen_context: expired capture %s", handle_id)

    def _cancel_expiry(self, handle_id: str) -> None:
        timer = self._expiry_timers.pop(handle_id, None)
        if timer is not None:
            timer.cancel()

    def discard_all(self) -> int:
        """Drop every held capture immediately (the panic button)."""
        with self._handle_lock:
            count = len(self._handles)
            self._handles.clear()
            for timer in self._expiry_timers.values():
                timer.cancel()
            self._expiry_timers.clear()
            return count

    def close(self) -> int:
        """Reject in-flight stores and remove every pixel held by this instance."""
        with self._handle_lock:
            self._closed = True
            return self.discard_all()

    @property
    def held_count(self) -> int:
        with self._handle_lock:
            self.sweep()
            return len(self._handles)

    # ---- bus -------------------------------------------------------------

    async def _announce(self, target: CaptureTarget, *, trace_id: UUID) -> bool:
        """Publish the pre-capture announcement. ``False`` if nobody heard it.

        A missing or slow bus degrades to a recorded limitation rather than to
        a refused capture: a broken indicator must not brick the feature, but
        it must never be invisible either (AP-30).
        """
        if self._bus is None:
            return False
        waiter = None
        try:
            from jarvis.core.events import ScreenCaptureAnnounced  # noqa: PLC0415
            from jarvis.screen_context import indicator  # noqa: PLC0415

            # Minimal recording fakes intentionally expose only ``publish``.
            # The real EventBus exposes ``subscribe`` and therefore uses the
            # renderer acknowledgement rather than equating publish with sight.
            truthful_ack = callable(getattr(self._bus, "subscribe", None))
            if truthful_ack:
                waiter = indicator.prepare(trace_id)

            await asyncio.wait_for(
                self._bus.publish(
                    ScreenCaptureAnnounced(
                        trace_id=trace_id,
                        target_kind=str(target.kind),
                        target_label=_safe_target_label(target),
                        reason=str(target.reason),
                    )
                ),
                timeout=_ANNOUNCE_TIMEOUT_S,
            )
            if not truthful_ack:
                return True
            assert waiter is not None
            return await asyncio.wait_for(
                asyncio.shield(waiter), timeout=_ANNOUNCE_TIMEOUT_S
            )
        except TimeoutError:
            log.warning(
                "screen_context: capture indicator did not acknowledge within "
                "%.1fs — capturing without an on-screen signal",
                _ANNOUNCE_TIMEOUT_S,
            )
            return False
        except Exception:  # noqa: BLE001 — a subscriber bug must not stop a capture
            log.warning("screen_context: capture announcement failed", exc_info=True)
            return False
        finally:
            if waiter is not None:
                indicator.discard(trace_id)

    async def _dismiss_indicator(self, *, trace_id: UUID) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.screen_context.indicator import (  # noqa: PLC0415
                ScreenCaptureIndicatorDismissed,
            )

            await self._bus.publish(
                ScreenCaptureIndicatorDismissed(trace_id=trace_id)
            )
        except Exception:  # noqa: BLE001 - hiding is best effort but observable
            log.warning(
                "screen_context: capture indicator dismissal failed",
                exc_info=True,
            )

    async def _publish_completed(
        self, context: ScreenContext, *, trace_id: UUID
    ) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import ScreenCaptureCompleted  # noqa: PLC0415

            await self._bus.publish(
                ScreenCaptureCompleted(
                    trace_id=trace_id,
                    target_kind=str(context.target.kind),
                    target_label=_safe_target_label(context.target),
                    width=context.size[0],
                    height=context.size[1],
                    bytes_size=context.byte_size,
                    redaction_count=len(context.redactions.hits),
                    degradation_count=len(context.degradations),
                    ui_text_source=context.ui_text_source,
                )
            )
        except Exception:  # noqa: BLE001 - receipt failure cannot erase the capture
            log.warning(
                "screen_context: capture receipt publication failed",
                exc_info=True,
            )

    async def _publish_grabbed(
        self, size: tuple[int, int], *, trace_id: UUID
    ) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import ScreenCaptureGrabbed  # noqa: PLC0415

            await self._bus.publish(
                ScreenCaptureGrabbed(
                    trace_id=trace_id,
                    width=size[0],
                    height=size[1],
                )
            )
        except Exception:  # noqa: BLE001 - an audio receipt cannot erase pixels
            log.warning(
                "screen_context: shutter receipt publication failed",
                exc_info=True,
            )


def _safe_target_label(target: CaptureTarget) -> str:
    """Metadata-only target label; never expose an app or document title."""
    if target.kind is TargetKind.WINDOW:
        return "active window"
    return f"monitor {target.monitor_name}" if target.monitor_name else "selected monitor"


def _encode(image: Any) -> tuple[bytes, tuple[int, int]]:
    """Downscale to the vision budget, then JPEG within the byte ceiling."""
    from PIL import Image  # noqa: PLC0415

    width, height = image.size
    longest = max(width, height)
    if longest > _MAX_DIMENSION:
        factor = _MAX_DIMENSION / longest
        image = image.resize(
            (max(1, round(width * factor)), max(1, round(height * factor))),
            resample=Image.Resampling.LANCZOS,
        )

    quality = _JPEG_QUALITY
    while True:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()
        if len(data) <= _MAX_IMAGE_BYTES:
            return data, image.size
        if quality > _MIN_JPEG_QUALITY:
            quality = max(_MIN_JPEG_QUALITY, quality - 10)
            continue

        # High-entropy frames remain large even at the minimum useful JPEG
        # quality. Continue shrinking until the advertised byte ceiling is a
        # fact, not a best effort.
        shrink = min(0.9, math.sqrt(_MAX_IMAGE_BYTES / len(data)) * 0.95)
        new_size = (
            max(1, int(image.size[0] * shrink)),
            max(1, int(image.size[1] * shrink)),
        )
        if new_size == image.size:
            return data, image.size
        image = image.resize(new_size, resample=Image.Resampling.LANCZOS)


def _rects_intersect_or_unknown(
    window: tuple[int, int, int, int] | None,
    monitor: tuple[int, int, int, int],
) -> bool:
    """Whether a window may be visible in a monitor capture (fail closed)."""
    if window is None:
        return True
    wl, wt, ww, wh = window
    ml, mt, mw, mh = monitor
    return wl < ml + mw and wl + ww > ml and wt < mt + mh and wt + wh > mt


# --------------------------------------------------------------------------
# Config bridge
# --------------------------------------------------------------------------


def settings_from_config(cfg: Any) -> ScreenContextSettings:
    """Build settings from a ``JarvisConfig``, tolerating an older config file.

    Every value is read defensively: a config written by an older version has
    no ``[screen_context]`` block at all, and that must produce working
    defaults rather than an AttributeError on the voice path.
    """
    block = getattr(cfg, "screen_context", None)
    if block is None:
        return ScreenContextSettings()
    main_monitor = "primary"
    with contextlib.suppress(Exception):
        main_monitor = str(getattr(cfg.computer_use, "main_monitor", "primary"))
    return ScreenContextSettings(
        enabled=bool(getattr(block, "enabled", True)),
        denylist=tuple(getattr(block, "denylist", ()) or ()),
        extra_patterns=tuple(getattr(block, "sensitive_patterns", ()) or ()),
        include_default_patterns=bool(
            getattr(block, "include_default_patterns", True)
        ),
        max_text_chars=int(getattr(block, "max_text_chars", 4000)),
        ttl_s=float(getattr(block, "ttl_s", 120.0)),
        ocr_enabled=bool(getattr(block, "ocr_enabled", False)),
        main_monitor=main_monitor,
    )


__all__ = [
    "CaptureOutcome",
    "CaptureStatus",
    "ScreenContextService",
    "ScreenContextSettings",
    "settings_from_config",
]
