"""Immutable data model for the Screen Context service.

Every type here is ``frozen=True``: a capture is a finished fact, and the
service, the redactor and the consumer all hand the same object around. A
mutable context would let a later layer quietly widen what an earlier layer
decided to redact.

Two modelling choices carry the feature's privacy promise:

* :class:`ScreenContext` holds image **bytes**, never a path. A path implies a
  file, and a file is persistence — which this feature does not do without an
  explicit, separate act of consent (see ``docs/screen-context.md``).
* Absence is never rendered as emptiness. A capture that could not read UI text
  carries a :class:`Degradation` saying so, so neither the model nor the user
  can mistake "we could not look" for "there was nothing there" (AP-30).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------


class VisualIntent(StrEnum):
    """How clearly the user asked Jarvis to look at the screen.

    Three-valued on purpose. A two-valued gate has to resolve every unclear
    utterance as either a capture or a miss; both are wrong for the same
    inputs ("what is that?" — a question about the screen, or about the topic
    under discussion?). ``AMBIGUOUS`` is what lets Jarvis ask instead of guess.
    """

    #: No screen reference at all — the overwhelmingly common case.
    NONE = "none"
    #: A screen reference is plausible but not established. Ask, never capture.
    AMBIGUOUS = "ambiguous"
    #: Unambiguous: the user wants Jarvis to look at the screen.
    SCREEN = "screen"
    #: Unambiguous AND scoped to the focused window/app/document.
    WINDOW = "window"


@dataclass(frozen=True, slots=True)
class IntentVerdict:
    """The classifier's answer plus the evidence it is based on.

    ``evidence`` exists so a wrong verdict is debuggable from a log line alone
    — the alternative is re-deriving the regex match by hand from a transcript
    that may no longer exist.
    """

    intent: VisualIntent
    #: The literal matched fragment(s) of the utterance, for logs and receipts.
    evidence: tuple[str, ...] = ()
    #: Locale the classification ran under (BCP-47 base, e.g. ``"de"``).
    locale: str = ""

    @property
    def wants_capture(self) -> bool:
        """True only for the two unambiguous verdicts."""
        return self.intent in (VisualIntent.SCREEN, VisualIntent.WINDOW)

    @property
    def needs_clarification(self) -> bool:
        return self.intent is VisualIntent.AMBIGUOUS


# --------------------------------------------------------------------------
# Targeting
# --------------------------------------------------------------------------


class TargetKind(StrEnum):
    MONITOR = "monitor"
    WINDOW = "window"


class TargetReason(StrEnum):
    """Why this surface was chosen — shown in the receipt, logged on capture.

    The user asking "why did it photograph that screen?" deserves an answer
    that does not require reading the source.
    """

    #: The monitor the mouse cursor was on at trigger time (the normal path).
    CURSOR_MONITOR = "cursor_monitor"
    #: Cursor unreadable — fell back to the monitor hosting the on-screen bar.
    BAR_MONITOR = "bar_monitor"
    #: Neither cursor nor bar available — fell back to the OS primary monitor.
    PRIMARY_MONITOR = "primary_monitor"
    #: The utterance scoped the request to the focused window.
    FOCUSED_WINDOW = "focused_window"
    #: Window scope was asked for but the window rect was unusable.
    WINDOW_FALLBACK_MONITOR = "window_fallback_monitor"


@dataclass(frozen=True, slots=True)
class WindowFacts:
    """What is in front, per the OS — independent of any pixels.

    Populated even for a monitor-scoped capture: "which app was the user in"
    is context the model needs and the accessibility layer answers cheaply.
    All fields degrade to their empty value rather than raising.
    """

    app_name: str = ""
    title: str = ""
    pid: int = 0
    #: ``(left, top, width, height)`` in the platform's input units.
    frame_rect: tuple[int, int, int, int] | None = None

    @property
    def is_known(self) -> bool:
        return bool(self.app_name or self.title)


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    """The exact surface a capture will grab, decided before any pixels move."""

    kind: TargetKind
    #: ``(left, top, width, height)`` in virtual-desktop coordinates.
    bbox: tuple[int, int, int, int]
    reason: TargetReason
    #: Human-readable monitor identity (mss name, or a synthesized one).
    monitor_name: str = ""
    window: WindowFacts = field(default_factory=WindowFacts)
    #: Native window handle when the capture is window-scoped, else ``None``.
    window_handle: int | None = None

    @property
    def width(self) -> int:
        return int(self.bbox[2])

    @property
    def height(self) -> int:
        return int(self.bbox[3])

    @property
    def is_usable(self) -> bool:
        """A degenerate rect (zero/negative extent) is never captured."""
        return self.width > 0 and self.height > 0


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------


class RedactionRule(StrEnum):
    """Which rule removed something. Carried into the report, not just logged."""

    #: An accessibility node the OS itself marks as a secure/password field.
    PASSWORD_FIELD = "password_field"  # noqa: S105 — a rule name, not a credential
    #: Text matched a configured sensitive pattern.
    SENSITIVE_PATTERN = "sensitive_pattern"
    #: The whole capture was refused because the app is on the denylist.
    BLOCKED_APP = "blocked_app"


@dataclass(frozen=True, slots=True)
class RedactionHit:
    """One removal: which rule, which label, and where (if it had a location)."""

    rule: RedactionRule
    #: Configured label of the matching pattern, e.g. ``"card"`` or ``"iban"``.
    label: str = ""
    #: Image region blacked out, ``(left, top, width, height)``, image-local.
    region: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class RedactionReport:
    """What was removed before the context left the process.

    Travels *with* the context to the model. Telling the model "two regions
    were redacted" is strictly better than handing it a picture with black
    boxes and no explanation, which invites it to narrate the boxes as UI.
    """

    hits: tuple[RedactionHit, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def region_count(self) -> int:
        return sum(1 for h in self.hits if h.region is not None)

    @property
    def text_count(self) -> int:
        return sum(1 for h in self.hits if h.region is None)

    def summary(self) -> str:
        """One English line for the receipt and the model-facing note."""
        if self.is_empty:
            return "no redactions"
        parts: list[str] = []
        if self.region_count:
            parts.append(f"{self.region_count} image region(s) blacked out")
        if self.text_count:
            parts.append(f"{self.text_count} text match(es) replaced")
        labels = sorted({h.label for h in self.hits if h.label})
        tail = f" ({', '.join(labels)})" if labels else ""
        return "; ".join(parts) + tail


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


class DegradationCode(StrEnum):
    """Named, machine-readable reasons a capture is less than complete.

    A string message alone would be untestable and untranslatable; the code is
    the contract and the message is the explanation.
    """

    NO_CURSOR = "no_cursor"
    NO_BAR_POSITION = "no_bar_position"
    NO_WINDOW_FACTS = "no_window_facts"
    NO_UI_TEXT = "no_ui_text"
    UI_TEXT_TRUNCATED = "ui_text_truncated"
    WINDOW_RECT_UNUSABLE = "window_rect_unusable"
    INDICATOR_UNAVAILABLE = "indicator_unavailable"
    OCR_UNAVAILABLE = "ocr_unavailable"


@dataclass(frozen=True, slots=True)
class Degradation:
    """One thing that did not work, in a form the user can be told about."""

    code: DegradationCode
    #: English, user-facing, actionable where an action exists.
    message: str


# --------------------------------------------------------------------------
# The context itself
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScreenContext:
    """One capture, redacted, ready to hand to a conversation turn.

    Held in memory by :class:`~jarvis.screen_context.service.ScreenContextService`
    behind a single-use handle with a TTL. Contains no path: this object *is*
    the capture, and when the last reference goes, the capture is gone.
    """

    #: Encoded image bytes (JPEG by default), already redacted.
    image: bytes
    mime: str
    #: Pixel dimensions of the encoded image (post-downscale).
    size: tuple[int, int]
    target: CaptureTarget
    #: Visible UI text from the accessibility layer, scrubbed and truncated.
    ui_text: str = ""
    #: Where ``ui_text`` came from: ``"accessibility"``, ``"ocr"``, ``"none"``.
    ui_text_source: str = "none"
    redactions: RedactionReport = field(default_factory=RedactionReport)
    degradations: tuple[Degradation, ...] = ()
    captured_at_ns: int = 0

    @property
    def byte_size(self) -> int:
        return len(self.image)

    def describe(self) -> str:
        """One English line naming what was captured — the receipt text.

        Deliberately concrete about scope and dimensions, but never includes an
        app or document title because receipts enter metadata/event logs.
        """
        if self.target.kind is TargetKind.WINDOW:
            where = "active window"
        else:
            where = f"monitor {self.target.monitor_name or '?'}"
        bits = [f"captured {where}", f"{self.size[0]}x{self.size[1]}"]
        if not self.redactions.is_empty:
            bits.append(self.redactions.summary())
        if self.degradations:
            bits.append(f"{len(self.degradations)} limitation(s)")
        return ", ".join(bits)


__all__ = [
    "CaptureTarget",
    "Degradation",
    "DegradationCode",
    "IntentVerdict",
    "RedactionHit",
    "RedactionReport",
    "RedactionRule",
    "ScreenContext",
    "TargetKind",
    "TargetReason",
    "VisualIntent",
    "WindowFacts",
]
