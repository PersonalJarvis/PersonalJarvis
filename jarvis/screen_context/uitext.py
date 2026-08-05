"""Visible on-screen text — accessibility first, OCR only to fill a gap.

The accessibility tree is the right source and OCR is the fallback, not the
other way around, for three reasons that all point the same way: the OS already
knows the text exactly (no transcription errors), it knows the *structure*
(a button's label is not body text), and it costs single-digit milliseconds
against OCR's hundreds. A capture path that OCR'd by default would be slower and
less accurate at the same time.

OCR therefore runs only when all three hold simultaneously:

* the accessibility path produced (near-)nothing,
* the user enabled it, and
* a backend is actually installed.

That last condition is why OCR is not a dependency. The base install stays
torch-free (CLAUDE.md §3), so this module *probes* for a backend and degrades
with a named reason when there is none — it never pulls one in.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jarvis.screen_context.models import Degradation, DegradationCode
from jarvis.screen_context.ports import Rect

log = logging.getLogger(__name__)

#: Below this many characters, accessibility text counts as "nothing useful"
#: and OCR (if enabled) may supplement it. A window legitimately can have very
#: little text; the threshold is low enough not to trigger on those.
_SPARSE_TEXT_THRESHOLD = 24

#: Roles whose text is chrome, not content — dropped before aggregation so the
#: budget is spent on what the user is actually looking at.
_CHROME_ROLES: frozenset[str] = frozenset(
    {"ScrollBar", "Separator", "Thumb", "TitleBar", "Splitter"}
)


def nodes_in_rect(
    nodes: Iterable[Any],
    rect: Rect,
    *,
    keep_unbounded: bool,
) -> tuple[Any, ...]:
    """Nodes whose bounds intersect ``rect``.

    A monitor-scoped capture must not carry text from a window on a *different*
    monitor: the accessibility tree spans the whole desktop, and handing the
    model text it cannot see in the image is how "it described something that
    was not on my screen" happens.

    A node with no bounds is retained only for a window-scoped capture. The
    active window identity then binds the whole tree to the captured surface.
    A monitor capture cannot place an unbounded node on one monitor safely, so
    keeping it could expose text from another display.
    """
    left, top, width, height = (int(v) for v in rect)
    right, bottom = left + width, top + height
    kept: list[Any] = []
    for node in nodes:
        bounds = getattr(node, "bounds", None)
        if not bounds or len(bounds) != 4 or all(int(v) == 0 for v in bounds):
            if keep_unbounded:
                kept.append(node)
            continue
        nx, ny, nw, nh = (int(v) for v in bounds)
        if nx < right and nx + nw > left and ny < bottom and ny + nh > top:
            kept.append(node)
    return tuple(kept)


def aggregate_text(nodes: Iterable[Any], *, max_chars: int) -> tuple[str, bool]:
    """Join node labels into one text block. Returns ``(text, truncated)``.

    Password-marked nodes are dropped *here*, at the source, rather than
    scrubbed later: their value should never enter the string in the first
    place. Duplicates are collapsed because accessibility trees repeat a label
    across a control and its wrapper, and paying tokens three times for the same
    word crowds out text the user actually asked about.
    """
    seen: set[str] = set()
    parts: list[str] = []
    total = 0
    truncated = False

    for node in nodes:
        if bool(getattr(node, "is_password", False)):
            continue
        if str(getattr(node, "role", "") or "") in _CHROME_ROLES:
            continue
        for raw in (getattr(node, "name", ""), getattr(node, "value", "")):
            text = " ".join(str(raw or "").split())
            if not text or text in seen:
                continue
            seen.add(text)
            if total + len(text) + 1 > max_chars:
                truncated = True
                return ("\n".join(parts), truncated)
            parts.append(text)
            total += len(text) + 1

    return ("\n".join(parts), truncated)


def _observation_matches_target(
    observation: Any,
    *,
    expected_pid: int,
    expected_window_title: str,
) -> bool:
    """Whether accessibility data still belongs to the captured foreground."""
    observed_pid = int(getattr(observation, "active_pid", 0) or 0)
    if expected_pid and observed_pid:
        return expected_pid == observed_pid

    expected_title = " ".join(expected_window_title.casefold().split())
    observed_title = " ".join(
        str(getattr(observation, "window_title", "") or "").casefold().split()
    )
    if expected_title and observed_title:
        return expected_title == observed_title

    # With an expected identity but no comparable field, accepting the tree
    # would risk attaching text from a window focused after the shutter.
    return not (expected_pid or expected_title)


async def read_ui_text(
    reader: Any,
    *,
    target_rect: Rect,
    max_chars: int,
    keep_unbounded_nodes: bool,
    window_title_filter: str | None = None,
    expected_pid: int = 0,
    expected_window_title: str = "",
) -> tuple[str, str, tuple[Any, ...], tuple[Degradation, ...]]:
    """Read visible text for ``target_rect``.

    Returns ``(text, source, nodes, degradations)``. ``nodes`` is handed back
    because the redactor needs the same node list to black out secure fields in
    the image — reading the tree twice would be both slower and racy (the
    second read can see a different screen).

    ``source`` is ``"accessibility"``, ``"none"``, and never a lie: a host
    without an accessibility layer gets ``"none"`` plus a degradation, so no
    caller can mistake "we could not read" for "there was no text" (AP-30).
    """
    degradations: list[Degradation] = []

    observation = await reader.read(window_title_filter=window_title_filter)
    if observation is None:
        degradations.append(
            Degradation(
                code=DegradationCode.NO_UI_TEXT,
                message=(
                    "On-screen text could not be read on this system, so only "
                    "the image was used. On Linux this needs an AT-SPI session; "
                    "on macOS it needs the accessibility permission."
                ),
            )
        )
        return ("", "none", (), tuple(degradations))

    if not _observation_matches_target(
        observation,
        expected_pid=expected_pid,
        expected_window_title=expected_window_title,
    ):
        degradations.append(
            Degradation(
                code=DegradationCode.NO_UI_TEXT,
                message=(
                    "The active window changed while on-screen text was being "
                    "read, so that text was discarded instead of attaching it "
                    "to a different screenshot."
                ),
            )
        )
        return ("", "none", (), tuple(degradations))

    nodes = nodes_in_rect(
        getattr(observation, "nodes", ()) or (),
        target_rect,
        keep_unbounded=keep_unbounded_nodes,
    )
    text, truncated = aggregate_text(nodes, max_chars=max_chars)
    if truncated:
        degradations.append(
            Degradation(
                code=DegradationCode.UI_TEXT_TRUNCATED,
                message=(
                    f"The screen contained more text than the {max_chars}-character "
                    "limit, so it was shortened."
                ),
            )
        )
    if not text:
        return ("", "none", nodes, tuple(degradations))
    return (text, "accessibility", nodes, tuple(degradations))


# --------------------------------------------------------------------------
# OCR supplement
# --------------------------------------------------------------------------


def text_is_sparse(
    text: str,
    *,
    image_size: tuple[int, int] | None = None,
) -> bool:
    """Whether accessibility text is too thin for the captured surface."""
    threshold = _SPARSE_TEXT_THRESHOLD
    if image_size is not None:
        width, height = image_size
        threshold = min(256, max(threshold, int(width * height / 25_000)))
    return len(text.strip()) < threshold


@dataclass(frozen=True, slots=True)
class OcrTextRegion:
    """One OCR line and its image-local bounding rectangle."""

    text: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class OcrSupplement:
    """OCR text plus the geometry needed to burn secrets out of pixels."""

    text: str = ""
    regions: tuple[OcrTextRegion, ...] = ()
    degradation: Degradation | None = None


def _ocr_unavailable(message: str) -> OcrSupplement:
    return OcrSupplement(
        degradation=Degradation(
            code=DegradationCode.OCR_UNAVAILABLE,
            message=message,
        )
    )


def ocr_supplement_with_regions(image: Any) -> OcrSupplement:
    """Run OCR once and retain line boxes for deterministic pixel redaction."""
    try:
        import pytesseract  # noqa: PLC0415
    except ImportError:
        log.info(
            "screen_context: OCR is enabled but no OCR engine is installed; "
            "OCR-based pixel redaction was unavailable."
        )
        return _ocr_unavailable(
            "Text recognition is switched on but no OCR engine is installed, "
            "so OCR-based pixel redaction was unavailable."
        )

    try:
        output = getattr(getattr(pytesseract, "Output", None), "DICT", "dict")
        data = pytesseract.image_to_data(image, output_type=output)
        texts = list(data.get("text", ()))
        grouped: dict[tuple[int, int, int], list[tuple[str, int, int, int, int]]] = {}
        for index, raw_text in enumerate(texts):
            word = " ".join(str(raw_text or "").split())
            if not word:
                continue
            try:
                left = int(data["left"][index])
                top = int(data["top"][index])
                width = int(data["width"][index])
                height = int(data["height"][index])
                key = (
                    int(data.get("block_num", list(range(len(texts))))[index]),
                    int(data.get("par_num", [0] * len(texts))[index]),
                    int(data.get("line_num", list(range(len(texts))))[index]),
                )
            except (IndexError, KeyError, TypeError, ValueError):  # OCR rows can be sparse.
                continue
            if width <= 0 or height <= 0:
                continue
            grouped.setdefault(key, []).append((word, left, top, width, height))

        regions: list[OcrTextRegion] = []
        for words in grouped.values():
            left = min(word[1] for word in words)
            top = min(word[2] for word in words)
            right = max(word[1] + word[3] for word in words)
            bottom = max(word[2] + word[4] for word in words)
            regions.append(
                OcrTextRegion(
                    text=" ".join(word[0] for word in words),
                    bounds=(left, top, right - left, bottom - top),
                )
            )
        return OcrSupplement(
            text="\n".join(region.text for region in regions),
            regions=tuple(regions),
        )
    except Exception as exc:  # noqa: BLE001 - optional binary/backend failures
        log.debug("OCR failed", exc_info=True)
        return _ocr_unavailable(
            f"Text recognition could not run ({exc}), so OCR-based pixel "
            "redaction was unavailable."
        )


def ocr_supplement(image: Any) -> tuple[str, Degradation | None]:
    """Best-effort OCR over the captured image.

    Probes for an installed backend rather than depending on one: the base
    install must stay torch-free and work on a slim container, so an absent
    backend is a normal, named outcome — not an error and not a silent empty
    string.

    Returns ``(text, degradation)``; exactly one of them is meaningful.
    """
    try:
        import pytesseract  # noqa: PLC0415
    except ImportError:
        log.info(
            "screen_context: OCR is enabled in settings but no OCR engine is "
            "installed — the capture used the image only. Install one to add "
            "text recognition for windows without an accessibility layer."
        )
        return (
            "",
            Degradation(
                code=DegradationCode.OCR_UNAVAILABLE,
                message=(
                    "Text recognition is switched on but no OCR engine is "
                    "installed, so only the image was used."
                ),
            ),
        )
    try:
        return (str(pytesseract.image_to_string(image) or "").strip(), None)
    except Exception as exc:  # noqa: BLE001 — tesseract binary missing / unreadable
        log.debug("OCR failed", exc_info=True)
        return (
            "",
            Degradation(
                code=DegradationCode.OCR_UNAVAILABLE,
                message=(
                    f"Text recognition could not run ({exc}), so only the image "
                    "was used."
                ),
            ),
        )


__all__ = [
    "OcrSupplement",
    "OcrTextRegion",
    "aggregate_text",
    "nodes_in_rect",
    "ocr_supplement",
    "ocr_supplement_with_regions",
    "read_ui_text",
    "text_is_sparse",
]
