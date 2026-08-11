"""Turn a :class:`~jarvis.visuals.spec.VisualSpec` into one self-contained page.

The serving constraints are the same ones that shape ``mission_graph``, and
they are strict enough to be worth restating: the page is delivered inside the
app origin under ``VIEW_CSP`` (``default-src 'none'; style-src 'unsafe-inline';
img-src data:``). No JavaScript, no fonts, no images, nothing fetched. Whatever
the page does, it does with markup and CSS that ship inside it.

Where ``mission_graph`` answers that with Python-computed absolute positions —
it draws curved connector tracks, which need real coordinates — this renderer
uses ordinary flow layout: flex rows, indented lists, a grid. The reason is the
input. A mission map has a fixed shape the renderer knows in advance; these
pictures carry model-authored labels of unpredictable length in five different
shapes. Text that reflows survives that; text baked into computed boxes does
not, and there is no JavaScript here to measure and correct it afterwards.

Every dynamic string is model-authored and escaped on the way in.

Pure function of its input: no clock, no filesystem, no randomness — the same
spec renders byte-identically, which is what makes the golden tests possible.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

from jarvis.visuals.brand import BRAND
from jarvis.visuals.spec import VisualItem, VisualSpec

# The widest a bar can be drawn, and the sliver a zero keeps so that "none" is
# still visibly a row rather than a missing one.
_BAR_MAX_PCT = 100.0
_BAR_MIN_PCT = 1.5


def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _format_number(value: float) -> str:
    """A number as a person would write it: no trailing ``.0``, thousands split."""
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    rounded = round(value, 2)
    return f"{rounded:,}".rstrip("0").rstrip(".")


def _detail(item: VisualItem) -> str:
    return f'<p class="detail">{_e(item.detail)}</p>' if item.detail else ""


# --- The five shapes ---------------------------------------------------------


def _render_flow(items: Iterable[VisualItem]) -> str:
    """Ordered steps, each followed by an arrow into the next.

    The arrow is its own flex child rather than a ``::after`` on the card, so
    that when the row wraps the arrow wraps with it instead of pointing off the
    end of a line.
    """
    parts: list[str] = []
    for index, item in enumerate(items):
        if index:
            parts.append('<div class="arrow" aria-hidden="true">→</div>')
        parts.append(
            '<div class="card step">'
            f'<div class="num">{index + 1}</div>'
            f'<div class="card-text"><p class="label">{_e(item.label)}</p>'
            f"{_detail(item)}</div>"
            "</div>"
        )
    return f'<div class="flow">{"".join(parts)}</div>'


def _render_hierarchy(items: Iterable[VisualItem], *, depth: int = 0) -> str:
    """Parts within parts. The left rule and the tick are drawn by CSS borders.

    Recursive, and bounded by the spec's depth cap rather than by anything
    here — validation already refused anything deeper.
    """
    rows: list[str] = []
    for item in items:
        children = (
            _render_hierarchy(item.children, depth=depth + 1) if item.children else ""
        )
        rows.append(
            '<li class="branch">'
            '<div class="card node">'
            f'<div class="card-text"><p class="label">{_e(item.label)}</p>'
            f"{_detail(item)}</div>"
            "</div>"
            f"{children}"
            "</li>"
        )
    klass = "tree root" if depth == 0 else "tree"
    return f'<ul class="{klass}">{"".join(rows)}</ul>'


def _render_comparison(items: Iterable[VisualItem]) -> str:
    """Options side by side. Auto-fit grid, so two read as two and six as six."""
    cards = "".join(
        '<div class="card option">'
        f'<p class="label">{_e(item.label)}</p>'
        f"{_detail(item)}"
        + (
            f'<p class="figure">{_e(_format_number(item.value))}</p>'
            if item.value is not None
            else ""
        )
        + "</div>"
        for item in items
    )
    return f'<div class="grid">{cards}</div>'


def _render_timeline(items: Iterable[VisualItem]) -> str:
    """Moments in order down a spine. The spine is a border on the list."""
    rows = "".join(
        '<li class="moment">'
        f'<div class="card-text"><p class="label">{_e(item.label)}</p>'
        f"{_detail(item)}</div>"
        "</li>"
        for item in items
    )
    return f'<ul class="timeline">{rows}</ul>'


def _render_bars(items: list[VisualItem]) -> str:
    """Quantities against each other.

    The scale starts at zero — or at the lowest negative value, when there is
    one — because a bar chart whose axis starts somewhere convenient overstates
    every difference on it. Widths are the one thing computed in Python here;
    there is no JavaScript to do it in the page.
    """
    values = [item.value for item in items if item.value is not None]
    baseline = min([0.0, *values]) if values else 0.0
    top = max(values) if values else 0.0
    span = top - baseline

    rows: list[str] = []
    for item in items:
        if item.value is None:
            width, figure = 0.0, "—"
        else:
            share = 1.0 if span <= 0 else (item.value - baseline) / span
            width = max(_BAR_MIN_PCT, share * _BAR_MAX_PCT)
            figure = _format_number(item.value)
        rows.append(
            '<li class="bar-row">'
            f'<div class="bar-label"><span class="label">{_e(item.label)}</span>'
            f"{_detail(item)}</div>"
            '<div class="bar-track">'
            f'<div class="bar-fill" style="width:{width:.1f}%"></div></div>'
            f'<div class="bar-figure">{_e(figure)}</div>'
            "</li>"
        )
    return f'<ul class="bars">{"".join(rows)}</ul>'


# One branch per entry in VISUAL_KINDS. A parity test pins the two together, so
# adding a kind without a renderer fails the suite instead of the user's turn.
_RENDERERS = {
    "flow": lambda items: _render_flow(items),
    "hierarchy": lambda items: _render_hierarchy(items),
    "comparison": lambda items: _render_comparison(items),
    "timeline": lambda items: _render_timeline(items),
    "bars": lambda items: _render_bars(list(items)),
}


def _css() -> str:
    b = BRAND
    return (
        "*{box-sizing:border-box}"
        "body{margin:0;padding:38px 34px 46px;background:" + b["bg"] + ";"
        "color:" + b["text"] + ";font:15px/1.55 'Segoe UI',system-ui,-apple-system,"
        "'Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased}"
        # header
        "header{display:flex;align-items:center;gap:14px;margin-bottom:6px}"
        ".mark{width:12px;height:12px;border-radius:3px;flex:none;"
        "background:" + b["primary"] + ";box-shadow:0 0 16px " + b["primary_glow"] + "}"
        ".kicker{font-size:11px;letter-spacing:.16em;text-transform:uppercase;"
        "color:" + b["primary"] + ";font-weight:600}"
        "h1{margin:2px 0 0;font-size:26px;line-height:1.25;font-weight:650;"
        "letter-spacing:-.01em}"
        ".meta{margin:14px 0 30px;color:" + b["text_muted"] + ";font-size:13px}"
        # shared card
        ".card{background:" + b["bg_card"] + ";border:1px solid " + b["border"] + ";"
        "border-radius:14px;padding:14px 16px}"
        ".label{margin:0;font-weight:600;font-size:15px;color:" + b["text"] + "}"
        ".detail{margin:5px 0 0;font-size:13px;line-height:1.5;"
        "color:" + b["text_muted"] + "}"
        # flow
        ".flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:10px}"
        ".flow .step{display:flex;gap:12px;align-items:flex-start;"
        "flex:1 1 210px;min-width:180px;max-width:340px}"
        ".num{flex:none;width:24px;height:24px;border-radius:8px;"
        "background:" + b["primary"] + ";color:" + b["bg"] + ";font-size:12px;"
        "font-weight:700;display:flex;align-items:center;justify-content:center}"
        ".arrow{flex:none;align-self:center;color:" + b["primary_deep"] + ";"
        "font-size:19px;line-height:1;padding:0 2px}"
        # hierarchy
        ".tree{list-style:none;margin:0;padding:0}"
        ".tree .tree{margin:8px 0 2px 14px;padding-left:18px;"
        "border-left:1px solid " + b["border_strong"] + "}"
        ".branch{position:relative;margin:0 0 10px}"
        ".tree .tree .branch::before{content:'';position:absolute;left:-18px;"
        "top:22px;width:14px;height:1px;background:" + b["border_strong"] + "}"
        ".node{border-left:2px solid " + b["primary_deep"] + "}"
        # comparison
        ".grid{display:grid;gap:12px;"
        "grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}"
        ".figure{margin:10px 0 0;font-size:22px;font-weight:700;"
        "color:" + b["primary"] + ";letter-spacing:-.01em}"
        # timeline
        ".timeline{list-style:none;margin:0;padding:0 0 0 26px;"
        "border-left:2px solid " + b["border_strong"] + "}"
        ".moment{position:relative;padding:0 0 22px}"
        ".moment:last-child{padding-bottom:0}"
        ".moment::before{content:'';position:absolute;left:-33px;top:4px;"
        "width:11px;height:11px;border-radius:50%;background:" + b["primary"] + ";"
        "border:2px solid " + b["bg"] + ";box-shadow:0 0 10px " + b["primary_glow"] + "}"
        # bars
        ".bars{list-style:none;margin:0;padding:0}"
        ".bar-row{display:grid;grid-template-columns:minmax(120px,26%) 1fr auto;"
        "gap:14px;align-items:center;padding:9px 0}"
        ".bar-label .detail{margin-top:2px}"
        ".bar-track{background:" + b["bg_elevated"] + ";border-radius:7px;height:26px;"
        "border:1px solid " + b["border"] + ";overflow:hidden}"
        ".bar-fill{height:100%;border-radius:6px;background:linear-gradient(90deg,"
        + b["primary_deep"]
        + ","
        + b["primary"]
        + ")}"
        ".bar-figure{font-variant-numeric:tabular-nums;font-weight:650;"
        "color:" + b["primary"] + ";min-width:56px;text-align:right}"
        # footer
        "footer{margin-top:32px;padding-top:16px;font-size:12px;"
        "color:" + b["text_faint"] + ";border-top:1px solid " + b["border"] + "}"
        # A narrow window (the detached Visualization window can be narrow)
        # collapses the bar grid rather than squeezing three columns into it.
        "@media (max-width:560px){.bar-row{grid-template-columns:1fr auto;}"
        ".bar-track{grid-column:1/-1}}"
    )


def render_visual_html(spec: VisualSpec) -> str:
    """Return the complete, self-contained page for ``spec``."""
    body = _RENDERERS[spec.kind](spec.items)
    caption = f"<footer>{_e(spec.caption)}</footer>" if spec.caption else ""
    meta = (
        f'<p class="meta">{_e(spec.source_utterance)}</p>'
        if spec.source_utterance
        else ""
    )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(spec.title)}</title>"
        f"<style>{_css()}</style></head><body>"
        '<header><span class="mark"></span>'
        '<div><div class="kicker">Visualisation</div>'
        f"<h1>{_e(spec.title)}</h1></div></header>"
        f"{meta}"
        f"{body}"
        f"{caption}"
        "</body></html>"
    )


__all__ = ["render_visual_html"]
