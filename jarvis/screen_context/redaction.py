"""Privacy filtering — runs before a capture leaves the process.

Three mechanisms, in escalating order of bluntness:

1. **App denylist** — a window whose app or title matches is never captured at
   all. Not captured-and-then-filtered: *not captured*. The distinction is the
   whole point. A password manager's window is not a picture with some secrets
   in it that can be scrubbed out; treating it as one means the secret existed
   in a buffer, and one refactor away from a log line.
2. **Region redaction** — accessibility nodes the OS marks as secure fields,
   and nodes whose text matches a sensitive pattern, are filled with opaque
   black in the raw pixels **before** encoding. Drawing on the raw frame
   matters: layering a box over an already-encoded image leaves the original
   bytes underneath.
3. **Text scrubbing** — the aggregated UI text runs through the same patterns;
   matches become a typed placeholder rather than disappearing, so the model is
   told that something was there and does not confabulate around a gap.

Patterns are configurable and additive. Each carries a label that reaches the
:class:`~jarvis.screen_context.models.RedactionReport`, which travels with the
context — the user can see what was removed, and the model is told rather than
handed unexplained black rectangles to narrate as UI.

Deliberately NOT here: any attempt to detect sensitive content *visually*
(a model that spots a password field in pixels). That would be a model call on
the capture path, it would be probabilistic where this must be deterministic,
and it would have to see the unredacted image to decide — defeating the purpose.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jarvis.screen_context.models import (
    RedactionHit,
    RedactionReport,
    RedactionRule,
    WindowFacts,
)
from jarvis.screen_context.ports import Rect

log = logging.getLogger(__name__)

_UNSAFE_CUSTOM_REGEX_RE = re.compile(
    r"\\[1-9]|\(\?P=|"
    r"\((?:\\.|[^()])*(?:[+*]|\{\d*,?\d*\})(?:\\.|[^()])*\)"
    r"(?:[+*]|\{\d*,?\d*\})|"
    r"\((?:\\.|[^()])*\|(?:\\.|[^()])*\)(?:[+*]|\{\d*,?\d*\})"
)


@dataclass(frozen=True, slots=True)
class SensitivePattern:
    """One labelled rule for text that must not reach the model."""

    label: str
    pattern: re.Pattern[str]

    @property
    def placeholder(self) -> str:
        return f"[redacted:{self.label}]"


def validate_pattern_source(source: str) -> str | None:
    """Return a validation error for custom regexes that can stall a turn."""
    if len(source) > 500:
        return "the expression exceeds 500 characters"
    if _UNSAFE_CUSTOM_REGEX_RE.search(source):
        return (
            "nested repetition, repeated alternation, and backreferences are "
            "not allowed in Screen Context patterns"
        )
    try:
        re.compile(source, re.IGNORECASE)
    except re.error as exc:  # Returning the parser message is the validation result.
        return str(exc)
    return None


def _compile(
    label: str,
    source: str,
    *,
    validate_custom: bool = False,
) -> SensitivePattern | None:
    """Compile one configured pattern; a bad regex is skipped, never fatal.

    A user-supplied pattern that fails to compile must not brick the whole
    feature — but it must also not silently reduce protection, so it is logged
    at WARNING with the label (AP-30).
    """
    validation_error = validate_pattern_source(source) if validate_custom else None
    if validation_error is not None:
        log.warning(
            "screen_context: sensitive pattern %r was skipped because %s. "
            "Content matching it will NOT be redacted.",
            label,
            validation_error,
        )
        return None
    try:
        return SensitivePattern(label=label, pattern=re.compile(source, re.IGNORECASE))
    except re.error as exc:
        log.warning(
            "screen_context: sensitive pattern %r is not a valid regular "
            "expression and was skipped (%s). Content matching it will NOT be "
            "redacted.",
            label,
            exc,
        )
        return None


# Shipped defaults. Chosen for shapes that are unambiguous enough to match
# without a false-positive storm: a 13-19 digit run in card grouping, an IBAN's
# country+checksum prefix, common API-key prefixes, and auth headers.
#
# Deliberately NOT included by default: bare email addresses and phone numbers.
# They appear in almost every window (a mail client, a contact list), and
# blacking them out by default would gut the feature's usefulness while the
# user is looking at exactly the screen they asked about. They are one config
# entry away for anyone who wants them.
_DEFAULT_PATTERN_SOURCES: tuple[tuple[str, str], ...] = (
    ("card", r"\b(?:\d[ -]?){13,19}\b"),
    ("iban", r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b"),
    ("api_key", r"\b(?:sk|pk|rk|api|key|token)[-_][A-Za-z0-9_-]{16,}\b"),
    # The scheme word must be consumed ALONG WITH the credential: a pattern
    # ending at the first ``\S+`` after the colon eats only "Bearer" and leaves
    # the token itself in the text — a redaction that reports success and
    # removes nothing.
    ("bearer", r"\b(?:authorization|x-api-key)\s*[:=]\s*(?:bearer\s+)?\S+"),
    ("bearer", r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    ("secret_assignment", r"\b(?:password|passwd|secret|passphrase)\s*[:=]\s*\S+"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def default_patterns() -> tuple[SensitivePattern, ...]:
    compiled = (_compile(label, source) for label, source in _DEFAULT_PATTERN_SOURCES)
    return tuple(p for p in compiled if p is not None)


def build_patterns(extra: Iterable[str] = (), *, include_defaults: bool = True):
    """Default patterns plus user-configured ones.

    Each extra entry is ``"label:regex"`` — the label is what the user sees in
    the redaction report, so an unlabelled pattern gets the generic ``custom``
    rather than showing a raw regex in the UI.
    """
    patterns: list[SensitivePattern] = list(default_patterns()) if include_defaults else []
    for entry in extra:
        raw = str(entry or "").strip()
        if not raw:
            continue
        label, _, source = raw.partition(":")
        if not source:
            label, source = "custom", raw
        compiled = _compile(
            label.strip() or "custom",
            source.strip(),
            validate_custom=True,
        )
        if compiled is not None:
            patterns.append(compiled)
    return tuple(patterns)


# --------------------------------------------------------------------------
# Layer 1 — app denylist
# --------------------------------------------------------------------------


def blocked_by_denylist(facts: WindowFacts, denylist: Iterable[str]) -> str | None:
    """The matching denylist entry, or ``None`` when capture may proceed.

    Matching is a case-insensitive substring test against the app name AND the
    window title. Substring rather than exact: the same application is
    ``"1Password"``, ``"1Password 8"`` and ``"1Password.exe"`` depending on
    platform and version, and an exact list would silently stop protecting
    after an update — a failure the user would never notice.
    """
    app = (facts.app_name or "").casefold()
    title = (facts.title or "").casefold()
    if not app and not title:
        return None
    for entry in denylist:
        needle = str(entry or "").strip().casefold()
        if not needle:
            continue
        if needle in app or needle in title:
            return str(entry).strip()
    return None


# --------------------------------------------------------------------------
# Layer 2 — image regions
# --------------------------------------------------------------------------


def regions_to_redact(
    nodes: Iterable[Any],
    *,
    target_bbox: Rect,
    patterns: Iterable[SensitivePattern],
    scale: float = 1.0,
) -> tuple[tuple[Rect, RedactionRule, str], ...]:
    """Image-local rectangles to black out, derived from accessibility nodes.

    Pure: takes node objects with ``bounds`` / ``is_password`` / ``name`` /
    ``value`` attributes and returns rectangles in the captured image's own
    pixel space. Kept free of any imaging library so it can be tested exactly
    the way it fails in production — with odd rects, off-screen nodes, and
    HiDPI scaling.

    ``scale`` converts the platform's input units to captured pixels. It is not
    always 1: macOS reports window and monitor geometry in points while
    ScreenCaptureKit returns backing pixels, so a Retina capture is 2x the rect
    it was asked for. Ignoring that would place every black box at a quarter of
    its intended size in the top-left corner — visibly wrong, and worse,
    leaving the secret visible.
    """
    left, top, width, height = (int(v) for v in target_bbox)
    out: list[tuple[Rect, RedactionRule, str]] = []
    pattern_list = list(patterns)

    for node in nodes:
        bounds = getattr(node, "bounds", None)
        if not bounds or len(bounds) != 4:
            continue

        rule: RedactionRule | None = None
        label = ""
        if bool(getattr(node, "is_password", False)):
            rule = RedactionRule.PASSWORD_FIELD
            label = "password_field"
        else:
            text = f"{getattr(node, 'name', '') or ''} {getattr(node, 'value', '') or ''}"
            if text.strip():
                for pattern in pattern_list:
                    if pattern.pattern.search(text):
                        rule = RedactionRule.SENSITIVE_PATTERN
                        label = pattern.label
                        break
        if rule is None:
            continue

        nx, ny, nw, nh = (int(v) for v in bounds)
        if nw <= 0 or nh <= 0:
            continue

        # Clip to the captured area, then translate into image-local space.
        clipped_left = max(nx, left)
        clipped_top = max(ny, top)
        clipped_right = min(nx + nw, left + width)
        clipped_bottom = min(ny + nh, top + height)
        if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
            continue  # entirely outside the captured surface

        out.append(
            (
                (
                    int((clipped_left - left) * scale),
                    int((clipped_top - top) * scale),
                    max(1, int((clipped_right - clipped_left) * scale)),
                    max(1, int((clipped_bottom - clipped_top) * scale)),
                ),
                rule,
                label,
            )
        )
    return tuple(out)


def apply_image_redactions(image: Any, regions) -> tuple[Any, tuple[RedactionHit, ...]]:
    """Fill ``regions`` with opaque black on a Pillow image, in place.

    Returns the image and the hits, so the caller never has to reconstruct what
    was drawn. A failure to draw is NOT swallowed into a "best effort" — if a
    region cannot be blacked out, the caller must know, because shipping the
    image anyway would leak exactly what this function exists to hide.
    """
    if not regions:
        return image, ()
    from PIL import ImageDraw  # noqa: PLC0415

    draw = ImageDraw.Draw(image)
    hits: list[RedactionHit] = []
    for rect, rule, label in regions:
        x, y, w, h = rect
        draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0))
        hits.append(RedactionHit(rule=rule, label=label, region=rect))
    return image, tuple(hits)


def local_text_regions_to_redact(
    regions: Iterable[Any],
    *,
    patterns: Iterable[SensitivePattern],
) -> tuple[tuple[Rect, RedactionRule, str], ...]:
    """Image-local OCR line rectangles whose text matches a privacy rule."""
    pattern_list = tuple(patterns)
    out: list[tuple[Rect, RedactionRule, str]] = []
    for region in regions:
        text = str(getattr(region, "text", "") or "")
        bounds = getattr(region, "bounds", None)
        if not text or not bounds or len(bounds) != 4:
            continue
        left, top, width, height = (int(value) for value in bounds)
        if width <= 0 or height <= 0:
            continue
        for pattern in pattern_list:
            if pattern.pattern.search(text):
                out.append(
                    (
                        (left, top, width, height),
                        RedactionRule.SENSITIVE_PATTERN,
                        pattern.label,
                    )
                )
                break
    return tuple(out)


# --------------------------------------------------------------------------
# Layer 3 — text
# --------------------------------------------------------------------------


def scrub_text(
    text: str, patterns: Iterable[SensitivePattern]
) -> tuple[str, tuple[RedactionHit, ...]]:
    """Replace sensitive matches with typed placeholders.

    A placeholder rather than deletion: a model handed text with a silent hole
    fills the hole. ``[redacted:card]`` tells it plainly that a card number was
    there and was withheld, which is both truthful and more useful.
    """
    if not text:
        return "", ()
    hits: list[RedactionHit] = []
    scrubbed = text
    for pattern in patterns:
        found = pattern.pattern.findall(scrubbed)
        if not found:
            continue
        scrubbed = pattern.pattern.sub(pattern.placeholder, scrubbed)
        hits.extend(
            RedactionHit(rule=RedactionRule.SENSITIVE_PATTERN, label=pattern.label)
            for _ in range(len(found))
        )
    return scrubbed, tuple(hits)


def merge_reports(*hit_groups: Iterable[RedactionHit]) -> RedactionReport:
    merged: list[RedactionHit] = []
    for group in hit_groups:
        merged.extend(group)
    return RedactionReport(hits=tuple(merged))


__all__ = [
    "SensitivePattern",
    "apply_image_redactions",
    "blocked_by_denylist",
    "build_patterns",
    "default_patterns",
    "local_text_regions_to_redact",
    "merge_reports",
    "regions_to_redact",
    "scrub_text",
    "validate_pattern_source",
]
