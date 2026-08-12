"""Render a mission archive as an n8n-style node-graph HTML page.

The Outputs archive can already *list* what a mission produced, but a flat file
list answers "which files exist", not "what happened". This module turns one
mission's archive into a single self-contained HTML page laid out like a
workflow editor: the mission on the left, its worker steps in the middle, the
deliverables they produced on the right, connected by curved tracks. One glance
answers "what ran, what came out, and how the pieces connect".

Serving constraints shape the whole design:

* The page is served in the app origin under the strict no-script CSP of the
  artifact ``/view`` route (``VIEW_CSP``: ``default-src 'none'``), so there is
  **no JavaScript**. All layout — node positions, canvas size, the bezier
  control points of every track — is computed here in Python and baked into
  the markup. Edges are one absolutely-positioned SVG layer; nodes are HTML
  ``<div>``s on top of it (HTML wraps and ellipsizes text, SVG ``<text>``
  cannot).
* Every dynamic string (utterance, step labels, filenames) is worker- or
  user-authored and is escaped before it reaches the page.
* The palette is the brand theme — matte black + signal yellow — kept in
  lockstep with the desktop app (``frontend/src/index.css`` dark tokens) and
  the video intro (``video/src/intro/theme.ts`` ``COLORS``).

Pure stdlib, no filesystem access: callers (``outputs_routes``) gather the
archive facts and pass plain data in, which keeps this renderer trivially
testable and reusable.
"""

from __future__ import annotations

import html
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jarvis.visuals.brand import BRAND

# --- Brand palette -----------------------------------------------------------
# ``BRAND`` moved to jarvis.visuals.brand once the on-demand visualisation
# renderer needed the same colours. It is imported at the top of this module
# and re-exported (see __all__): this module's ``BRAND`` is already part of its
# API for outputs_routes and the tests.

# Status → pill/track accent. Keys match OUTPUT_STATUSES in outputs_routes.
_STATUS_ACCENTS: Mapping[str, str] = {
    "success": BRAND["good"],
    "running": BRAND["primary"],
    "error": "#F87171",
    "cancelled": BRAND["text_muted"],
    "unknown": BRAND["text_faint"],
}

_STATUS_LABELS: Mapping[str, str] = {
    "success": "Success",
    "running": "Running",
    "error": "Failed",
    "cancelled": "Cancelled",
    "unknown": "Unknown",
}

# --- Graph model -------------------------------------------------------------


@dataclass(frozen=True)
class GraphFile:
    """One archived deliverable, relative to its step's ``artifacts/files/``."""

    path: str
    size: int


@dataclass(frozen=True)
class GraphStep:
    """One worker step (``tasks/<id>/``) and the deliverables it archived.

    ``tool_calls``/``failed_calls`` come from the reconstructed step timeline
    (:mod:`jarvis.ui.web.run_plan`) when a worker transcript exists; both stay
    zero for a run without a readable stream, and the subtitle then falls back
    to the file count alone.
    """

    step_id: str
    label: str
    files: tuple[GraphFile, ...]
    tool_calls: int = 0
    failed_calls: int = 0


@dataclass(frozen=True)
class MissionGraphData:
    """Everything the renderer needs, already collected by the caller."""

    slug: str
    title: str
    status: str
    summary: str | None
    duration_s: float | None
    steps: tuple[GraphStep, ...]


# Worktree-style task dir names carry a human label: ``01__refactor-router``.
_STEP_SLUG_RE = re.compile(r"^(?P<num>\d{1,3})__(?P<label>.+)$")


def _step_label(step_id: str, index: int) -> str:
    """A human name for a task directory.

    ``01__refactor-router`` → "01 · Refactor router" (worktree-style names carry
    their label). Kontrollierer-style names are UUID prefixes with nothing human
    in them, so those become "Step N" — honest and readable beats hex.
    """
    m = _STEP_SLUG_RE.match(step_id)
    if m:
        words = m.group("label").replace("-", " ").replace("_", " ").strip()
        pretty = words[:1].upper() + words[1:] if words else step_id
        return f"{m.group('num')} · {pretty}"
    return f"Step {index + 1}"


def build_mission_graph(
    slug: str,
    *,
    utterance: str | None,
    status: str,
    summary: str | None,
    duration_s: float | None,
    task_ids: Sequence[str],
    files: Sequence[Mapping[str, object]],
    plan_steps: Sequence[Mapping[str, object]] = (),
) -> MissionGraphData:
    """Assemble the graph model from archive facts.

    *files* entries are the ``/artifacts`` listing shape (``path`` like
    ``tasks/<id>/artifacts/files/<rel>``, plus ``size``). Files are grouped
    under their step; *task_ids* keeps steps visible even when they archived
    nothing — a step that ran and produced no deliverable is a fact worth
    showing, not hiding.

    *plan_steps* is the (optional) reconstructed timeline from
    :func:`jarvis.ui.web.run_plan.build_run_plan` — each entry's ``step_id``
    is ``"<task_id>:<seq>"``, which is aggregated here into per-step tool-call
    and failure counts. Purely additive: no transcript, no counts, same graph.
    """
    calls_by_task: dict[str, int] = {}
    failed_by_task: dict[str, int] = {}
    for plan_step in plan_steps:
        # Reasoning steps are the worker's own thoughts, not tool calls — the
        # per-task call count stays a count of ACTIONS.
        if plan_step.get("kind") == "reasoning":
            continue
        step_id = str(plan_step.get("step_id", ""))
        task_id, _, _seq = step_id.partition(":")
        if not task_id:
            continue
        calls_by_task[task_id] = calls_by_task.get(task_id, 0) + 1
        if plan_step.get("status") == "failed":
            failed_by_task[task_id] = failed_by_task.get(task_id, 0) + 1
    by_step: dict[str, list[GraphFile]] = {task_id: [] for task_id in task_ids}
    for entry in files:
        path = str(entry.get("path", ""))
        parts = path.replace("\\", "/").split("/")
        # Deliverable paths are tasks/<id>/artifacts/files/<rel> — anything
        # else is not archive-shaped and is skipped rather than guessed at.
        if len(parts) < 5 or parts[0] != "tasks" or parts[2] != "artifacts" or parts[3] != "files":
            continue
        step_id = parts[1]
        rel = "/".join(parts[4:])
        size = entry.get("size", 0)
        by_step.setdefault(step_id, []).append(
            GraphFile(path=rel, size=int(size) if isinstance(size, (int, float)) else 0)
        )

    steps = tuple(
        GraphStep(
            step_id=step_id,
            label=_step_label(step_id, index),
            files=tuple(sorted(step_files, key=lambda f: f.path)),
            tool_calls=calls_by_task.get(step_id, 0),
            failed_calls=failed_by_task.get(step_id, 0),
        )
        for index, (step_id, step_files) in enumerate(sorted(by_step.items()))
    )
    return MissionGraphData(
        slug=slug,
        title=(utterance or "").strip() or slug,
        status=status if status in _STATUS_ACCENTS else "unknown",
        summary=(summary or "").strip() or None,
        duration_s=duration_s,
        steps=steps,
    )


# --- Layout ------------------------------------------------------------------
# Fixed geometry, computed server-side. A column per node kind, left to right:
# mission → steps → deliverables, the n8n reading direction.

_MARGIN = 64
_COL_GAP = 112
_ROW_GAP = 16
_GROUP_GAP = 40

_MISSION_W, _MISSION_H = 272, 116
_STEP_W, _STEP_H = 240, 78
_FILE_W, _FILE_H = 256, 58
_OVERFLOW_H = 42

# Per step, at most this many file nodes are drawn individually; the rest
# collapse into one "+N more" node so a 92-file mission stays one readable
# screen instead of a two-metre column. The cap is stated on the node itself —
# a hidden file must read as "collapsed", never as "not produced".
MAX_FILES_PER_STEP = 6


@dataclass(frozen=True)
class _Placed:
    """A node with resolved geometry, ready to print."""

    kind: str  # mission | step | file | overflow
    x: int
    y: int
    w: int
    h: int
    title: str
    subtitle: str
    badge: str = ""


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _step_subtitle(step: GraphStep) -> str:
    """ "12 tool calls · 2 files" — only the parts the archive can actually back."""
    bits: list[str] = []
    if step.tool_calls:
        bits.append(f"{step.tool_calls} tool call{'s' if step.tool_calls != 1 else ''}")
    bits.append(f"{len(step.files)} file{'s' if len(step.files) != 1 else ''}")
    return " · ".join(bits)


def _file_badge(path: str) -> str:
    ext = posixpath.splitext(path)[1].lstrip(".").upper()
    return ext[:6] if ext else "FILE"


def _layout(
    data: MissionGraphData,
) -> tuple[list[_Placed], list[tuple[float, float, float, float]], int, int]:
    """Place every node and route every edge.

    Returns ``(nodes, edges, canvas_w, canvas_h)`` where each edge is the
    ``(x1, y1, x2, y2)`` port pair a bezier is drawn between. Steps and their
    file stacks share one vertical band per step, so neither column can
    overlap itself; the mission node centers on the whole span.
    """
    mission_x = _MARGIN
    step_x = mission_x + _MISSION_W + _COL_GAP
    file_x = step_x + _STEP_W + _COL_GAP

    # Height of each step's band: the taller of the step node itself and its
    # visible file stack (capped + optional overflow node).
    bands: list[tuple[GraphStep, list[GraphFile], int, int]] = []
    for step in data.steps:
        visible = list(step.files[:MAX_FILES_PER_STEP])
        hidden = len(step.files) - len(visible)
        stack_h = len(visible) * _FILE_H + max(0, len(visible) - 1) * _ROW_GAP
        if hidden > 0:
            stack_h += (_ROW_GAP if visible else 0) + _OVERFLOW_H
        bands.append((step, visible, hidden, max(_STEP_H, stack_h)))

    total_h = sum(band_h for *_rest, band_h in bands)
    total_h += max(0, len(bands) - 1) * _GROUP_GAP
    total_h = max(total_h, _MISSION_H)

    nodes: list[_Placed] = []
    edges: list[tuple[float, float, float, float]] = []

    mission_y = _MARGIN + (total_h - _MISSION_H) // 2
    file_count = sum(len(step.files) for step in data.steps)
    mission = _Placed(
        kind="mission",
        x=mission_x,
        y=mission_y,
        w=_MISSION_W,
        h=_MISSION_H,
        title=data.title,
        subtitle=f"{len(data.steps)} step{'s' if len(data.steps) != 1 else ''}"
        f" · {file_count} deliverable{'s' if file_count != 1 else ''}",
        badge=_STATUS_LABELS.get(data.status, "Unknown"),
    )
    nodes.append(mission)
    mission_port = (mission_x + _MISSION_W, mission_y + _MISSION_H / 2)

    cursor = _MARGIN
    for step, visible, hidden, band_h in bands:
        step_y = cursor + (band_h - _STEP_H) // 2
        step_node = _Placed(
            kind="step",
            x=step_x,
            y=step_y,
            w=_STEP_W,
            h=_STEP_H,
            title=step.label,
            subtitle=_step_subtitle(step),
            badge=f"{step.failed_calls} failed" if step.failed_calls else "",
        )
        nodes.append(step_node)
        edges.append((*mission_port, step_x, step_y + _STEP_H / 2))
        step_port = (step_x + _STEP_W, step_y + _STEP_H / 2)

        stack_h = len(visible) * _FILE_H + max(0, len(visible) - 1) * _ROW_GAP
        if hidden > 0:
            stack_h += (_ROW_GAP if visible else 0) + _OVERFLOW_H
        file_y = cursor + (band_h - stack_h) // 2 if stack_h else cursor
        for deliverable in visible:
            nodes.append(
                _Placed(
                    kind="file",
                    x=file_x,
                    y=file_y,
                    w=_FILE_W,
                    h=_FILE_H,
                    title=posixpath.basename(deliverable.path) or deliverable.path,
                    subtitle=_human_size(deliverable.size),
                    badge=_file_badge(deliverable.path),
                )
            )
            edges.append((*step_port, file_x, file_y + _FILE_H / 2))
            file_y += _FILE_H + _ROW_GAP
        if hidden > 0:
            nodes.append(
                _Placed(
                    kind="overflow",
                    x=file_x,
                    y=file_y,
                    w=_FILE_W,
                    h=_OVERFLOW_H,
                    title=f"+{hidden} more file{'s' if hidden != 1 else ''}",
                    subtitle="collapsed — see the Outputs list",
                )
            )
            edges.append((*step_port, file_x, file_y + _OVERFLOW_H / 2))
        cursor += band_h + _GROUP_GAP

    has_files = any(step.files for step in data.steps)
    right_edge = (
        file_x + _FILE_W
        if has_files
        else (step_x + _STEP_W if data.steps else mission_x + _MISSION_W)
    )
    canvas_w = right_edge + _MARGIN
    canvas_h = _MARGIN * 2 + total_h
    return nodes, edges, canvas_w, canvas_h


# --- Rendering ---------------------------------------------------------------

_ICONS: Mapping[str, str] = {
    # Inline SVGs are part of the document, so they render under the no-script
    # CSP where external images would be blocked. 16×16 viewBox, currentColor.
    "mission": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M3 14V2.5"/><path d="M3 3h9l-2 2.5L12 8H3"/></svg>'
    ),
    "step": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8l1-5.5Z"/></svg>'
    ),
    "file": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M4 1.5h5L13 5.5V14a.5.5 0 0 1-.5.5h-8A.5.5 0 0 1 4 14V1.5Z"/>'
        '<path d="M9 1.5V6h4"/></svg>'
    ),
}


def _css(data: MissionGraphData) -> str:
    accent = _STATUS_ACCENTS.get(data.status, BRAND["text_faint"])
    # A running mission's tracks flow (pure CSS, allowed by the CSP); a settled
    # mission's tracks hold still — motion means "in progress", nothing else.
    flow = (
        ".edge{stroke-dasharray:7 9;animation:track-flow 1.1s linear infinite}"
        "@keyframes track-flow{to{stroke-dashoffset:-16}}"
        if data.status == "running"
        else ""
    )
    return (
        "*{box-sizing:border-box;margin:0;padding:0}"
        f"body{{background:{BRAND['bg']};color:{BRAND['text']};"
        "font:14px/1.5 -apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;"
        "min-height:100vh}"
        f"header{{display:flex;align-items:center;gap:14px;padding:18px 28px;"
        f"background:{BRAND['bg_elevated']};border-bottom:1px solid {BRAND['border']}}}"
        f".mark{{width:12px;height:12px;border-radius:50%;background:{BRAND['primary']};"
        f"box-shadow:0 0 14px {BRAND['primary_glow']};flex:none}}"
        "h1{font-size:16px;font-weight:600;overflow:hidden;text-overflow:ellipsis;"
        "white-space:nowrap;min-width:0}"
        f".kicker{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;"
        f"color:{BRAND['text_muted']}}}"
        ".head-text{min-width:0;flex:1}"
        f".pill{{flex:none;font-size:11px;font-weight:600;letter-spacing:.08em;"
        f"text-transform:uppercase;padding:4px 12px;border-radius:999px;"
        f"color:{accent};border:1px solid {accent};background:{BRAND['bg_card']}}}"
        f".meta{{padding:12px 28px;color:{BRAND['text_muted']};font-size:12px;"
        f"border-bottom:1px solid {BRAND['border']};overflow:hidden;"
        "text-overflow:ellipsis;white-space:nowrap}"
        ".canvas-wrap{overflow:auto;padding:0}"
        ".canvas{position:relative;"
        f"background-image:radial-gradient(rgba(255,255,255,0.06) 1px,transparent 1px);"
        "background-size:22px 22px}"
        ".edges{position:absolute;inset:0;width:100%;height:100%}"
        f".edge{{fill:none;stroke:{BRAND['primary_deep']};stroke-opacity:.55;stroke-width:2}}"
        f".port{{fill:{BRAND['bg_card']};stroke:{BRAND['primary']};stroke-width:1.5}}"
        f".node{{position:absolute;display:flex;align-items:center;gap:10px;"
        f"padding:10px 14px;border-radius:10px;background:{BRAND['bg_card']};"
        f"border:1px solid {BRAND['border']};"
        "box-shadow:0 4px 16px rgba(0,0,0,0.45)}"
        f".node:hover{{border-color:{BRAND['border_strong']}}}"
        ".icon{flex:none;width:30px;height:30px;border-radius:8px;display:flex;"
        "align-items:center;justify-content:center}"
        ".icon svg{width:16px;height:16px}"
        f".node-mission{{border-color:rgba(255,214,10,0.45);"
        f"box-shadow:0 0 28px rgba(255,214,10,0.10),0 4px 16px rgba(0,0,0,0.45)}}"
        f".node-mission .icon{{background:rgba(255,214,10,0.12);color:{BRAND['primary']}}}"
        f".node-step .icon{{background:rgba(255,255,255,0.06);color:{BRAND['primary']}}}"
        f".node-file .icon{{background:rgba(255,255,255,0.05);color:{BRAND['text_muted']}}}"
        f".node-overflow{{justify-content:center;color:{BRAND['text_muted']};"
        f"border-style:dashed;background:{BRAND['bg_elevated']}}}"
        ".body{min-width:0;flex:1}"
        ".title{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;"
        "white-space:nowrap}"
        f".subtitle{{font-size:11px;color:{BRAND['text_muted']};overflow:hidden;"
        "text-overflow:ellipsis;white-space:nowrap}"
        f".badge{{flex:none;font-size:10px;font-weight:700;letter-spacing:.06em;"
        f"padding:2px 7px;border-radius:5px;background:rgba(255,214,10,0.10);"
        f"color:{BRAND['primary']}}}"
        f".node-mission .badge{{color:{accent};background:transparent;"
        f"border:1px solid {accent}}}"
        # A step's badge only ever announces failures — it reads as a warning.
        ".node-step .badge{color:#F87171;background:rgba(248,113,113,0.10)}"
        f"footer{{display:flex;gap:22px;padding:14px 28px;font-size:11px;"
        f"color:{BRAND['text_faint']};border-top:1px solid {BRAND['border']}}}"
        ".legend{display:flex;align-items:center;gap:7px}"
        ".swatch{width:9px;height:9px;border-radius:3px}" + flow
    )


def _edge_path(x1: float, y1: float, x2: float, y2: float) -> str:
    """The n8n curve: horizontal-out, horizontal-in, one cubic bezier."""
    bend = max(36.0, (x2 - x1) / 2)
    return (
        f"M {x1:.1f} {y1:.1f} "
        f"C {x1 + bend:.1f} {y1:.1f}, {x2 - bend:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}"
    )


def _render_node(node: _Placed) -> str:
    icon = _ICONS.get("file" if node.kind == "overflow" else node.kind, _ICONS["file"])
    badge = f'<span class="badge">{html.escape(node.badge)}</span>' if node.badge else ""
    if node.kind == "overflow":
        inner = (
            f'<div class="body"><div class="title">{html.escape(node.title)}</div>'
            f'<div class="subtitle">{html.escape(node.subtitle)}</div></div>'
        )
    else:
        kicker = '<div class="kicker">Mission</div>' if node.kind == "mission" else ""
        inner = (
            f'<div class="icon">{icon}</div>'
            f'<div class="body">{kicker}'
            f'<div class="title">{html.escape(node.title)}</div>'
            f'<div class="subtitle">{html.escape(node.subtitle)}</div></div>'
            f"{badge}"
        )
    style = f"left:{node.x}px;top:{node.y}px;width:{node.w}px;height:{node.h}px"
    return f'<div class="node node-{node.kind}" style="{style}">{inner}</div>'


def render_mission_graph_html(data: MissionGraphData) -> str:
    """Return the complete, self-contained mission-map document."""
    nodes, edges, canvas_w, canvas_h = _layout(data)

    paths = "".join(
        f'<path class="edge" d="{_edge_path(x1, y1, x2, y2)}"/>' for x1, y1, x2, y2 in edges
    )
    # Connection ports drawn over the track ends, the n8n affordance that makes
    # a line read as "plugged in" rather than decoration.
    ports = "".join(
        f'<circle class="port" cx="{x:.1f}" cy="{y:.1f}" r="4"/>'
        for x, y in {(x1, y1) for x1, y1, *_ in edges} | {(x2, y2) for *_ignored, x2, y2 in edges}
    )
    svg = (
        f'<svg class="edges" viewBox="0 0 {canvas_w} {canvas_h}" '
        f'width="{canvas_w}" height="{canvas_h}" aria-hidden="true">{paths}{ports}</svg>'
    )
    node_html = "".join(_render_node(node) for node in nodes)

    status_label = _STATUS_LABELS.get(data.status, "Unknown")
    meta_bits = [f"Session {data.slug}"]
    if data.duration_s is not None:
        meta_bits.append(_human_duration(data.duration_s))
    if data.summary:
        meta_bits.append(data.summary)
    meta = " · ".join(meta_bits)

    empty_note = (
        '<p class="meta">This mission archived no steps yet — the map fills in as work lands.</p>'
        if not data.steps
        else ""
    )

    title = f"{data.title} — Mission map"
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{_css(data)}</style></head><body>"
        '<header><span class="mark"></span>'
        '<div class="head-text"><div class="kicker">Mission map</div>'
        f"<h1>{html.escape(data.title)}</h1></div>"
        f'<span class="pill">{html.escape(status_label)}</span></header>'
        f'<p class="meta">{html.escape(meta)}</p>'
        f"{empty_note}"
        '<div class="canvas-wrap">'
        f'<div class="canvas" style="width:{canvas_w}px;height:{canvas_h}px">'
        f"{svg}{node_html}</div></div>"
        "<footer>"
        + "".join(
            f'<span class="legend"><span class="swatch" '
            f'style="background:{color}"></span>{label}</span>'
            for label, color in (
                ("Mission", BRAND["primary"]),
                ("Step", BRAND["text_muted"]),
                ("Deliverable", BRAND["text_faint"]),
            )
        )
        + "</footer></body></html>"
    )


__all__ = [
    "BRAND",
    "MAX_FILES_PER_STEP",
    "GraphFile",
    "GraphStep",
    "MissionGraphData",
    "build_mission_graph",
    "render_mission_graph_html",
]
