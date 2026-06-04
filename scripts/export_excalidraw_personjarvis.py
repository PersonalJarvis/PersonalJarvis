"""One-shot converter: MCP simplified Excalidraw elements -> excalidraw.com scene JSON.

Reads stdin (the elements array from the MCP checkpoint), expands inline `label` fields
into separate text elements bound via `containerId`, strips `cameraUpdate` pseudo-elements,
fills in the required Excalidraw element schema fields with defaults, and prints the full
scene JSON to stdout for upload via export_to_excalidraw.
"""

import json
import random
import sys
import time

FONT_FAMILY_EXCALIFONT = 5
LINE_HEIGHT = 1.25


def rand_seed() -> int:
    return random.randint(1, 2_000_000_000)


def base_element(el: dict) -> dict:
    return {
        "id": el["id"],
        "type": el["type"],
        "x": el["x"],
        "y": el["y"],
        "width": el.get("width", 0),
        "height": el.get("height", 0),
        "angle": 0,
        "strokeColor": el.get("strokeColor", "#1e1e1e"),
        "backgroundColor": el.get("backgroundColor", "transparent"),
        "fillStyle": el.get("fillStyle", "solid"),
        "strokeWidth": el.get("strokeWidth", 2),
        "strokeStyle": el.get("strokeStyle", "solid"),
        "roughness": el.get("roughness", 1),
        "opacity": el.get("opacity", 100),
        "groupIds": [],
        "frameId": None,
        "roundness": el.get("roundness"),
        "seed": rand_seed(),
        "versionNonce": rand_seed(),
        "version": 1,
        "isDeleted": False,
        "boundElements": [],
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
        "index": None,
    }


def text_element(
    el_id: str,
    x: float,
    y: float,
    text: str,
    font_size: int,
    stroke_color: str = "#1e1e1e",
    container_id: str | None = None,
    width: float | None = None,
    height: float | None = None,
) -> dict:
    estimated_width = width if width else max(20, len(text) * font_size * 0.55)
    estimated_height = height if height else font_size * LINE_HEIGHT
    return {
        "id": el_id,
        "type": "text",
        "x": x,
        "y": y,
        "width": estimated_width,
        "height": estimated_height,
        "angle": 0,
        "strokeColor": stroke_color,
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": rand_seed(),
        "versionNonce": rand_seed(),
        "version": 1,
        "isDeleted": False,
        "boundElements": [],
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
        "index": None,
        "text": text,
        "fontSize": font_size,
        "fontFamily": FONT_FAMILY_EXCALIFONT,
        "textAlign": "center" if container_id else "left",
        "verticalAlign": "middle" if container_id else "top",
        "baseline": int(font_size * 0.8),
        "containerId": container_id,
        "originalText": text,
        "lineHeight": LINE_HEIGHT,
        "autoResize": True,
    }


def convert(elements: list) -> list:
    out: list = []
    label_counter = 0
    for el in elements:
        t = el["type"]
        if t == "cameraUpdate":
            continue
        if t == "delete":
            continue
        if t == "restoreCheckpoint":
            continue

        if t in ("rectangle", "ellipse", "diamond"):
            shape = base_element(el)
            if "label" in el:
                label_counter += 1
                label_id = f"{el['id']}_lbl_{label_counter}"
                lbl = el["label"]
                font_size = lbl.get("fontSize", 16)
                txt = lbl["text"]
                tx = el["x"] + el["width"] / 2 - (len(txt) * font_size * 0.55) / 2
                ty = el["y"] + el["height"] / 2 - (font_size * LINE_HEIGHT) / 2
                text_el = text_element(
                    el_id=label_id,
                    x=tx,
                    y=ty,
                    text=txt,
                    font_size=font_size,
                    container_id=el["id"],
                )
                shape["boundElements"] = [{"id": label_id, "type": "text"}]
                out.append(shape)
                out.append(text_el)
            else:
                out.append(shape)
        elif t == "text":
            out.append(
                text_element(
                    el_id=el["id"],
                    x=el["x"],
                    y=el["y"],
                    text=el["text"],
                    font_size=el.get("fontSize", 16),
                    stroke_color=el.get("strokeColor", "#1e1e1e"),
                )
            )
        elif t == "arrow":
            arrow = base_element(el)
            arrow["points"] = el.get("points", [[0, 0], [el.get("width", 0), el.get("height", 0)]])
            arrow["lastCommittedPoint"] = None
            arrow["startBinding"] = el.get("startBinding")
            arrow["endBinding"] = el.get("endBinding")
            arrow["startArrowhead"] = el.get("startArrowhead")
            arrow["endArrowhead"] = el.get("endArrowhead", "arrow")
            arrow["elbowed"] = False
            if "label" in el:
                label_counter += 1
                label_id = f"{el['id']}_lbl_{label_counter}"
                lbl = el["label"]
                font_size = lbl.get("fontSize", 14)
                txt = lbl["text"]
                pts = arrow["points"]
                mx = el["x"] + (pts[0][0] + pts[-1][0]) / 2
                my = el["y"] + (pts[0][1] + pts[-1][1]) / 2
                tx = mx - (len(txt) * font_size * 0.55) / 2
                ty = my - (font_size * LINE_HEIGHT) / 2
                text_el = text_element(
                    el_id=label_id,
                    x=tx,
                    y=ty,
                    text=txt,
                    font_size=font_size,
                    container_id=el["id"],
                )
                arrow["boundElements"] = [{"id": label_id, "type": "text"}]
                out.append(arrow)
                out.append(text_el)
            else:
                out.append(arrow)
        else:
            out.append(base_element(el))
    return out


def main() -> None:
    in_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    if in_path:
        with open(in_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(sys.stdin.read())
    elements = data["elements"] if isinstance(data, dict) and "elements" in data else data
    converted = convert(elements)
    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": converted,
        "appState": {
            "gridSize": None,
            "viewBackgroundColor": "#ffffff",
        },
        "files": {},
    }
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(scene, f, ensure_ascii=False)
    else:
        json.dump(scene, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
