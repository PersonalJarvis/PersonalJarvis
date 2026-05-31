"""Render the MCP simplified Excalidraw elements as a standalone SVG.

Reads the checkpoint JSON (same simplified format used by mcp__claude_ai_Excalidraw__create_view)
and produces a self-contained .svg that opens natively in any browser. Strips cameraUpdate
pseudo-elements, expands inline `label` fields to centered SVG text, and approximates the
rough.js look with rounded corners but solid strokes (clean readable variant).
"""

import json
import math
import sys
import xml.sax.saxutils as su

PADDING = 40


def shape_to_svg(el: dict) -> str:
    t = el["type"]
    fill = el.get("backgroundColor", "transparent")
    stroke = el.get("strokeColor", "#1e1e1e")
    sw = el.get("strokeWidth", 2)
    opacity = el.get("opacity", 100) / 100.0
    rx = 8 if el.get("roundness") else 0
    dash = ""
    if el.get("strokeStyle") == "dashed":
        dash = ' stroke-dasharray="8 6"'
    elif el.get("strokeStyle") == "dotted":
        dash = ' stroke-dasharray="2 4"'
    x, y, w, h = el["x"], el["y"], el.get("width", 0), el.get("height", 0)

    if t == "rectangle":
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
            f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{sw}"{dash}/>'
        )
    if t == "ellipse":
        cx, cy = x + w / 2, y + h / 2
        return (
            f'<ellipse cx="{cx}" cy="{cy}" rx="{w / 2}" ry="{h / 2}" '
            f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{sw}"{dash}/>'
        )
    if t == "diamond":
        cx, cy = x + w / 2, y + h / 2
        pts = f"{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}"
        return (
            f'<polygon points="{pts}" '
            f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{sw}"{dash}/>'
        )
    if t == "arrow":
        pts = el.get("points", [[0, 0], [w, h]])
        abs_pts = [(x + dx, y + dy) for dx, dy in pts]
        path_d = "M " + " L ".join(f"{px},{py}" for px, py in abs_pts)
        arrow_mark = ' marker-end="url(#arrow)"' if el.get("endArrowhead") != None else ""
        if el.get("startArrowhead"):
            arrow_mark += ' marker-start="url(#arrow)"'
        return (
            f'<path d="{path_d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{dash}{arrow_mark}/>'
        )
    return ""


def text_to_svg(el: dict, label_for: dict | None = None) -> str:
    if label_for is not None:
        font_size = el.get("fontSize", 16)
        text = el["text"]
        cx = label_for["x"] + label_for.get("width", 0) / 2
        cy = label_for["y"] + label_for.get("height", 0) / 2
        return (
            f'<text x="{cx}" y="{cy}" font-family="Excalifont, Virgil, sans-serif" '
            f'font-size="{font_size}" fill="{el.get("strokeColor", "#1e1e1e")}" '
            f'text-anchor="middle" dominant-baseline="middle">{su.escape(text)}</text>'
        )
    font_size = el.get("fontSize", 16)
    text = el["text"]
    return (
        f'<text x="{el["x"]}" y="{el["y"] + font_size}" font-family="Excalifont, Virgil, sans-serif" '
        f'font-size="{font_size}" fill="{el.get("strokeColor", "#1e1e1e")}">{su.escape(text)}</text>'
    )


def arrow_label_svg(el: dict) -> str:
    lbl = el["label"]
    pts = el.get("points", [[0, 0], [el.get("width", 0), el.get("height", 0)]])
    mx = el["x"] + (pts[0][0] + pts[-1][0]) / 2
    my = el["y"] + (pts[0][1] + pts[-1][1]) / 2 - 6
    font_size = lbl.get("fontSize", 14)
    return (
        f'<text x="{mx}" y="{my}" font-family="Excalifont, Virgil, sans-serif" '
        f'font-size="{font_size}" fill="#1e1e1e" text-anchor="middle" '
        f'dominant-baseline="middle" style="paint-order:stroke;stroke:#ffffff;'
        f'stroke-width:4px;stroke-linejoin:round;">{su.escape(lbl["text"])}</text>'
    )


def render(elements: list) -> str:
    drawable = [e for e in elements if e["type"] not in ("cameraUpdate", "delete", "restoreCheckpoint")]

    xs = [e["x"] for e in drawable]
    ys = [e["y"] for e in drawable]
    rights = [e["x"] + e.get("width", 0) for e in drawable]
    bottoms = [e["y"] + e.get("height", 0) for e in drawable]
    min_x = min(xs) - PADDING
    min_y = min(ys) - PADDING
    max_x = max(rights) + PADDING
    max_y = max(bottoms) + PADDING
    vb_w = max_x - min_x
    vb_h = max_y - min_y

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} {min_y} {vb_w} {vb_h}" '
        f'preserveAspectRatio="xMidYMid meet" style="background:#ffffff;font-family:Excalifont,Virgil,sans-serif;">'
    )
    out.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>'
    )

    # Pass 1: shapes (rect/ellipse/diamond/arrow)
    for el in drawable:
        t = el["type"]
        if t in ("rectangle", "ellipse", "diamond", "arrow"):
            out.append(shape_to_svg(el))

    # Pass 2: labels (centered) and standalone text
    for el in drawable:
        t = el["type"]
        if t == "text":
            out.append(text_to_svg(el))
        elif t in ("rectangle", "ellipse", "diamond") and "label" in el:
            out.append(text_to_svg(el["label"] if isinstance(el["label"], dict) else {"text": el["label"], "fontSize": 16}, label_for=el))
        elif t == "arrow" and "label" in el:
            out.append(arrow_label_svg(el))

    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    in_path = sys.argv[1]
    out_path = sys.argv[2]
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    elements = data["elements"] if isinstance(data, dict) and "elements" in data else data
    svg = render(elements)
    html_wrapper = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<title>Personal Jarvis — Person <-> Jarvis Flow</title>'
        '<style>html,body{margin:0;padding:0;background:#fafafa;height:100%;}'
        'svg{width:100vw;height:100vh;display:block;}'
        '.frame{position:fixed;top:8px;left:8px;background:rgba(255,255,255,.85);'
        'padding:6px 12px;border-radius:6px;font-family:system-ui;font-size:13px;color:#444;}'
        '</style></head><body>'
        '<div class="frame">Personal Jarvis — Person &lt;-&gt; Jarvis end-to-end loop  ·  scroll &amp; pinch to zoom</div>'
        + svg
        + '</body></html>'
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_wrapper)
    print(f"wrote {out_path} ({len(html_wrapper)} bytes)")


if __name__ == "__main__":
    main()
