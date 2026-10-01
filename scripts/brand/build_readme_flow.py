"""Build the README "how a request flows" graphic in a dark and a light variant.

The README shows it through ``<picture>`` so GitHub picks the variant that
matches the reader's theme. Both files come from the one layout below; edit
the layout or a palette here and re-run instead of hand-editing the SVGs:

    python scripts/brand/build_readme_flow.py

The SVGs stay deliberately plain (no CSS, no animation, no filters, no
markers, no web fonts) because GitHub serves them as images through its
proxy and every renderer must draw them the same way. Every text run is
sized to fit its box with room to spare in the widest system font.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "assets" / "brand"

WIDTH, HEIGHT = 1200, 392
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

PALETTES = {
    "dark": {
        "bg": "#0d1117",
        "card": "#161b22",
        "card_line": "#30363d",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "icon": "#9fb0c3",
        "accent": "#eac56b",
        "accent_text": "#fff0bf",
        "jarvis_fill": "#2b2416",
        "bubble": "#21262d",
        "safe": "#3fb950",
        "monitor": "#58a6ff",
        "ask": "#d29922",
        "block": "#f85149",
        "chip_opacity": "0.14",
        "connector": "#484f58",
    },
    "light": {
        "bg": "#ffffff",
        "card": "#f6f8fa",
        "card_line": "#d0d7de",
        "text": "#1f2328",
        "muted": "#59636e",
        "icon": "#57606a",
        "accent": "#b7860b",
        "accent_text": "#5c4300",
        "jarvis_fill": "#fff8dc",
        "bubble": "#ffffff",
        "safe": "#1a7f37",
        "monitor": "#0969da",
        "ask": "#9a6700",
        "block": "#cf222e",
        "chip_opacity": "0.10",
        "connector": "#afb8c1",
    },
}

# Column boxes (x, width). Steps read left to right.
COL_ASK = (32, 216)
COL_JARVIS = (292, 140)
COL_ROUTES = (484, 280)
COL_GATE = (794, 120)
COL_RESULT = (954, 214)

TOP, BOTTOM = 76, 324  # full-height band shared by routes, gate and result
MID = (TOP + BOTTOM) // 2  # 200

ROUTES = [
    ("Answer", "Replies right away", "answer"),
    ("Act", "Desktop, browser, plugins, MCP", "act"),
    ("Delegate", "Agents, Codex, Claude Code", "delegate"),
]
ROUTE_H, ROUTE_GAP = 72, 16

RISK_LEVELS = [("safe", "safe"), ("monitor", "monitor"), ("ask you", "ask"), ("block", "block")]

HISTORY = ["Plan made", "Browser, 6 steps", "You approved 1 change", "Report saved"]

ICONS = {
    # 24x24 stroke icons, drawn at the given origin.
    "mic": (
        '<rect x="8" y="2" width="8" height="13" rx="4"/>'
        '<path d="M4.5 11a7.5 7.5 0 0 0 15 0M12 18.5V22"/>'
    ),
    "answer": '<path d="M4 5h16v11H9l-5 4z"/>',
    "act": '<path d="M5 3l13 7-6 1.5L9.5 18z"/><path d="M12.5 12.5 18 18"/>',
    "delegate": (
        '<circle cx="8" cy="8" r="3.2"/><circle cx="17" cy="9.5" r="2.6"/>'
        '<path d="M2.5 20c.6-3.8 2.8-5.8 5.5-5.8s4.9 2 5.5 5.8M14.5 15c2.6-.6 5.3.8 6 4.5"/>'
    ),
    "shield": (
        '<path d="M12 2.5 4.5 5.5v6c0 4.6 3.1 8.4 7.5 10 4.4-1.6 7.5-5.4 7.5-10v-6z"/>'
        '<path d="m8.8 12 2.3 2.3 4.2-4.6"/>'
    ),
    "check": '<circle cx="12" cy="12" r="10.5"/><path d="m7.5 12.3 3.1 3.1 6-6.4"/>',
}

SPARKLE = (
    "M11 0c.8 5.2 4.8 9.2 11 11-6.2 1.8-10.2 5.8-11 11"
    "-.8-5.2-4.8-9.2-11-11 6.2-1.8 10.2-5.8 11-11z"
)


def rect(x, y, w, h, r, fill, stroke="none", extra=""):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
        f'fill="{fill}" stroke="{stroke}"{extra}/>'
    )


def text(x, y, s, *, size, fill, weight=400, anchor="middle", spacing=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{extra}>{escape(s)}</text>'
    )


def icon(name, x, y, color, scale=1.0, width=1.8):
    return (
        f'<g transform="translate({x} {y}) scale({scale})" fill="none" '
        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{ICONS[name]}</g>'
    )


def arrow(x1, y1, x2, y2, color, curve=False):
    """A connector ending in a filled arrowhead pointing right at (x2, y2)."""
    if curve:
        mx = (x1 + x2) / 2
        d = f"M{x1} {y1}C{mx} {y1} {mx} {y2} {x2 - 7} {y2}"
    else:
        d = f"M{x1} {y1}H{x2 - 7}"
    head = f"M{x2} {y2}l-9-5v10z"
    return (
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>'
        f'<path d="{head}" fill="{color}"/>'
    )


def step_label(cx, number, word, p):
    return (
        f'<text x="{cx}" y="52" font-size="12" font-weight="700" text-anchor="middle" '
        f'letter-spacing="1.6"><tspan fill="{p["accent"]}">{number}</tspan>'
        f'<tspan fill="{p["muted"]}" dx="8">{escape(word)}</tspan></text>'
    )


def build(p: dict[str, str]) -> str:
    out: list[str] = []
    add = out.append
    add(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-labelledby="title desc">'
    )
    add('<title id="title">How a request flows through Personal Jarvis</title>')
    add(
        '<desc id="desc">1. You ask by voice or chat. 2. Jarvis decides how to handle it. '
        "3. It answers directly, acts with your desktop, browser, plugins or MCP tools, "
        "or delegates to an agent such as Codex or Claude Code. 4. Every action passes a "
        "risk check (safe, monitor, ask you, block), and you approve risky steps. "
        "5. The result comes back with a recorded run history.</desc>"
    )
    add(rect(0.5, 0.5, WIDTH - 1, HEIGHT - 1, 20, p["bg"], p["card_line"]))
    add(f'<g font-family="{FONT}">')

    # Step labels
    for (x, w), n, word in [
        (COL_ASK, "1", "ASK"),
        (COL_JARVIS, "2", "DECIDE"),
        (COL_ROUTES, "3", "DO"),
        (COL_GATE, "4", "CHECK"),
        (COL_RESULT, "5", "DELIVER"),
    ]:
        add(step_label(x + w / 2, n, word, p))

    # 1 You ask
    x, w = COL_ASK
    line, card = p["card_line"], p["card"]
    add(rect(x, 112, w, 176, 14, card, line))
    add(icon("mic", x + 18, 128, p["icon"], 0.9))
    add(text(x + 46, 146, "You", size=19, fill=p["text"], weight=700, anchor="start"))
    add(rect(x + 16, 162, w - 32, 66, 12, p["bubble"], line))
    add(text(x + 30, 190, "“Sort my downloads", size=14, fill=p["text"], anchor="start"))
    add(text(x + 30, 212, "and file the invoices.”", size=14, fill=p["text"], anchor="start"))
    for i, chip in enumerate(["voice", "chat"]):
        cx = x + 16 + i * 92
        add(rect(cx, 244, 80, 26, 13, "none", line))
        add(text(cx + 40, 262, chip, size=13, fill=p["muted"]))

    # 2 Jarvis decides
    jx, jw = COL_JARVIS
    add(arrow(x + w, MID, jx, MID, p["accent"]))
    add(rect(jx, MID - 64, jw, 128, 16, p["jarvis_fill"], p["accent"], ' stroke-width="2"'))
    sx, sy = jx + jw / 2 - 11, MID - 48
    add(f'<path transform="translate({sx} {sy})" d="{SPARKLE}" fill="{p["accent"]}"/>')
    add(text(jx + jw / 2, MID + 14, "Jarvis", size=22, fill=p["accent_text"], weight=700))
    add(text(jx + jw / 2, MID + 38, "picks the way", size=13, fill=p["muted"]))

    # 3 Routes
    rx, rw = COL_ROUTES
    total = 3 * ROUTE_H + 2 * ROUTE_GAP
    ry0 = MID - total / 2
    centres = []
    for i, (title, desc, ic) in enumerate(ROUTES):
        ry = ry0 + i * (ROUTE_H + ROUTE_GAP)
        cy = ry + ROUTE_H / 2
        centres.append(cy)
        add(arrow(jx + jw, MID, rx, cy, p["accent"], curve=True))
        add(rect(rx, ry, rw, ROUTE_H, 12, card, line))
        add(icon(ic, rx + 18, cy - 12, p["icon"]))
        add(text(rx + 56, cy - 4, title, size=16, fill=p["text"], weight=700, anchor="start"))
        add(text(rx + 56, cy + 17, desc, size=13, fill=p["muted"], anchor="start"))

    # 4 Risk check
    gx, gw = COL_GATE
    gc = gx + gw / 2
    for cy in centres:
        add(arrow(rx + rw, cy, gx, cy, p["connector"]))
    add(rect(gx, TOP, gw, BOTTOM - TOP, 14, card, line))
    add(icon("shield", gc - 12, TOP + 14, p["icon"]))
    add(text(gc, TOP + 58, "Risk check", size=14, fill=p["text"], weight=700))
    for i, (label, key) in enumerate(RISK_LEVELS):
        cy = TOP + 72 + i * 32
        c = p[key]
        tint = f' fill-opacity="{p["chip_opacity"]}" stroke-opacity="0.55"'
        add(rect(gx + 14, cy, gw - 28, 24, 12, c, c, tint))
        add(text(gc, cy + 17, label, size=13, fill=c, weight=600))
    add(text(gc, BOTTOM - 32, "You approve", size=12, fill=p["muted"]))
    add(text(gc, BOTTOM - 15, "risky steps", size=12, fill=p["muted"]))

    # 5 Result + run history
    ex, ew = COL_RESULT
    add(arrow(gx + gw, MID, ex, MID, p["accent"]))
    add(rect(ex, TOP, ew, BOTTOM - TOP, 14, card, line))
    add(icon("check", ex + 18, TOP + 18, p["safe"], width=2))
    add(text(ex + 50, TOP + 36, "Result", size=17, fill=p["text"], weight=700, anchor="start"))
    kinds = "Answer, file, or report"
    add(text(ex + 18, TOP + 66, kinds, size=13, fill=p["muted"], anchor="start"))
    add(f'<path d="M{ex + 18} {TOP + 84}H{ex + ew - 18}" stroke="{line}"/>')
    add(
        text(
            ex + 18, TOP + 110, "RUN HISTORY",
            size=11, fill=p["muted"], weight=700, anchor="start", spacing="1.4",
        )
    )
    hy0, step = TOP + 140, 28
    rail_end = hy0 - 4 + step * (len(HISTORY) - 1)
    add(f'<path d="M{ex + 24} {hy0 - 4}V{rail_end}" stroke="{line}" stroke-width="2"/>')
    for i, item in enumerate(HISTORY):
        hy = hy0 + i * step
        dot = f'<circle cx="{ex + 24}" cy="{hy - 4}" r="4" fill="{card}" '
        add(dot + f'stroke="{p["accent"]}" stroke-width="2"/>')
        add(text(ex + 40, hy, item, size=13, fill=p["text"], anchor="start"))

    # Footer
    add(
        text(
            WIDTH / 2,
            HEIGHT - 26,
            "Runs on your computer  ·  Any model, local or hosted  ·  Windows, macOS, Linux",
            size=13,
            fill=p["muted"],
        )
    )
    add("</g></svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    for name, palette in PALETTES.items():
        path = OUT_DIR / f"request-flow-{name}.svg"
        path.write_text(build(palette), encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
