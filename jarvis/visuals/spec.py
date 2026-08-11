"""What a caller has to supply for a picture, and how loose input becomes it.

The model supplies the STRUCTURE (five steps, three options, a tree of parts);
Python owns the drawing. That split is the whole reason this feature is cheap:
asking a model to emit HTML means hundreds of output tokens per picture, a
different layout every time, and a page nobody validated before serving it.
Asking for a dozen short labels costs a fraction of that, renders identically
on every run, and can be checked before a single byte reaches disk.

So this module is deliberately strict. Every limit below is a real defence:

* the item/child/depth caps bound both the output tokens a model can spend and
  the size of the page — a "visualise the whole codebase" answer degrades to a
  refusal with a reason, never to a 4 MB document;
* the text caps stop one runaway label from destroying a layout that has no
  JavaScript to reflow it;
* unknown kinds are rejected by name so the model's next attempt can be right,
  rather than silently falling back to a shape the user did not ask for.

:class:`VisualSpecError` carries a message written FOR the model — it is handed
back as the tool error, and a precise one turns a failed call into a corrected
retry instead of an apology to the user.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

# --- The five shapes -----------------------------------------------------
# One entry per branch in `render._render_body`; a parity test pins the three
# lists (this tuple, the tool schema enum, the renderer branches) together.
VISUAL_KINDS: Final[tuple[str, ...]] = (
    "flow",  # ordered steps, one arrow to the next
    "hierarchy",  # parts within parts, indented
    "comparison",  # options side by side
    "timeline",  # moments in order, down a spine
    "bars",  # quantities against each other
)

# Structure caps. See the module docstring for what each one defends.
MAX_ITEMS: Final = 12
MAX_CHILDREN: Final = 8
MAX_DEPTH: Final = 3
# Text caps. Generous enough for a real label, tight enough that no single
# string can break a JavaScript-free layout.
MAX_TITLE_CHARS: Final = 120
MAX_LABEL_CHARS: Final = 80
MAX_DETAIL_CHARS: Final = 220
MAX_CAPTION_CHARS: Final = 300


class VisualSpecError(ValueError):
    """Rejected input, with a message the model can act on.

    A ``ValueError`` subclass so a caller that only catches ``ValueError``
    still behaves, and a named type so the tool can tell "your spec was wrong"
    (retryable) apart from "the disk was full" (not).
    """


@dataclass(frozen=True)
class VisualItem:
    """One box, row, bar or moment.

    ``value`` is read only by ``bars`` and ``children`` only by ``hierarchy``;
    both are accepted (and ignored) elsewhere rather than rejected, because a
    model that supplies one field too many has still described the picture
    correctly, and failing that call would cost a whole retry for nothing.
    """

    label: str
    detail: str = ""
    value: float | None = None
    children: tuple[VisualItem, ...] = ()


@dataclass(frozen=True)
class VisualSpec:
    """A complete, validated description of one picture."""

    title: str
    kind: str
    items: tuple[VisualItem, ...]
    caption: str = ""
    # What the user actually said, kept for the archive's run label. Never
    # drawn on the page: the title is the model's summary of the request and
    # reads better; the raw utterance is metadata, not a heading.
    source_utterance: str = field(default="", compare=False)


def _clean_text(value: Any, *, limit: int, what: str, required: bool = False) -> str:
    """Collapse whitespace, enforce the cap, reject the empty required case.

    Truncation over rejection for over-long text: the model described the right
    picture and simply wrote too much, so a clipped label still produces the
    page the user asked for. A MISSING label is different — that is a hole in
    the structure, and the model has to fix it.
    """
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        raise VisualSpecError(f"{what} must be text, got {type(value).__name__}.")
    if required and not text:
        raise VisualSpecError(f"{what} must not be empty.")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _parse_value(raw: Any, *, where: str) -> float | None:
    """A bar length, or None when the item carries no quantity."""
    if raw is None or raw == "":
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise VisualSpecError(
            f"{where}: 'value' must be a number (or omitted), got {raw!r}."
        ) from None
    if number != number or number in (float("inf"), float("-inf")):  # NaN/inf
        raise VisualSpecError(f"{where}: 'value' must be a finite number.")
    return number


def _parse_items(
    raw: Any,
    *,
    where: str,
    depth: int,
    limit: int,
) -> tuple[VisualItem, ...]:
    """Validate one level of the item list, recursing into ``children``."""
    if isinstance(raw, Mapping) or isinstance(raw, (str, bytes)):
        raise VisualSpecError(f"{where} must be a list of items.")
    if not isinstance(raw, Sequence):
        raise VisualSpecError(f"{where} must be a list of items.")
    if not raw:
        raise VisualSpecError(f"{where} must contain at least one item.")
    if len(raw) > limit:
        raise VisualSpecError(
            f"{where} has {len(raw)} items; at most {limit} fit on one page. "
            f"Group them or draw the most important {limit}."
        )

    items: list[VisualItem] = []
    for index, entry in enumerate(raw):
        spot = f"{where}[{index}]"
        # A bare string is a perfectly clear item ("Build", "Test", "Ship") and
        # is the cheapest thing a model can emit, so it is accepted as shorthand.
        if isinstance(entry, str):
            label = _clean_text(entry, limit=MAX_LABEL_CHARS, what=spot, required=True)
            items.append(VisualItem(label=label))
            continue
        if not isinstance(entry, Mapping):
            raise VisualSpecError(f"{spot} must be an object with a 'label', or a plain string.")

        children: tuple[VisualItem, ...] = ()
        raw_children = entry.get("children")
        if raw_children:
            if depth >= MAX_DEPTH:
                raise VisualSpecError(
                    f"{spot}: nesting is capped at {MAX_DEPTH} levels — "
                    f"flatten the deeper parts into their parent's detail."
                )
            children = _parse_items(
                raw_children, where=f"{spot}.children", depth=depth + 1, limit=MAX_CHILDREN
            )

        items.append(
            VisualItem(
                label=_clean_text(
                    entry.get("label"), limit=MAX_LABEL_CHARS, what=f"{spot}.label", required=True
                ),
                detail=_clean_text(
                    entry.get("detail"), limit=MAX_DETAIL_CHARS, what=f"{spot}.detail"
                ),
                value=_parse_value(entry.get("value"), where=spot),
                children=children,
            )
        )
    return tuple(items)


def parse_spec(payload: Mapping[str, Any], *, source_utterance: str = "") -> VisualSpec:
    """Validate model-supplied arguments into a :class:`VisualSpec`.

    Raises:
        VisualSpecError: with a message aimed at the model, never at the user.
    """
    if not isinstance(payload, Mapping):
        raise VisualSpecError("The visualisation arguments must be an object.")

    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in VISUAL_KINDS:
        raise VisualSpecError(
            f"Unknown kind {kind or '(missing)'!r}. Pick one of: {', '.join(VISUAL_KINDS)}."
        )

    spec = VisualSpec(
        title=_clean_text(
            payload.get("title"), limit=MAX_TITLE_CHARS, what="'title'", required=True
        ),
        kind=kind,
        items=_parse_items(payload.get("items"), where="'items'", depth=1, limit=MAX_ITEMS),
        caption=_clean_text(payload.get("caption"), limit=MAX_CAPTION_CHARS, what="'caption'"),
        source_utterance=" ".join((source_utterance or "").split()),
    )

    # A bar chart with no numbers is a bullet list wearing a costume: every bar
    # would be full width and the picture would say nothing. Caught here rather
    # than papered over in the renderer, so the model can supply the numbers.
    if kind == "bars" and not any(item.value is not None for item in spec.items):
        raise VisualSpecError(
            "kind 'bars' needs a numeric 'value' on at least one item — "
            "without numbers there is nothing to compare. Use 'comparison' for "
            "options that have no quantity."
        )
    return spec


__all__ = [
    "MAX_CAPTION_CHARS",
    "MAX_CHILDREN",
    "MAX_DEPTH",
    "MAX_DETAIL_CHARS",
    "MAX_ITEMS",
    "MAX_LABEL_CHARS",
    "MAX_TITLE_CHARS",
    "VISUAL_KINDS",
    "VisualItem",
    "VisualSpec",
    "VisualSpecError",
    "parse_spec",
]
