"""Sample the captured local SVG profiles into Blender's retained outline input.

Authoring-only dependency: Playwright with Chromium. No website or API is used.
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

source = Path(__file__).resolve().parents[2] / "art/studies/agent-symbol-companions/source"
shapes = json.loads((source / "shapes.json").read_text(encoding="utf-8"))
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    outlines = {}
    for name, markup in shapes.items():
        page.set_content(markup)
        outlines[name] = page.locator("svg > :first-child").evaluate(
            """e => {
                const length = e.getTotalLength();
                return Array.from({length:96}, (_,i) => {
                    const p = e.getPointAtLength(length*i/96);
                    return [Number(p.x.toFixed(5)),Number(p.y.toFixed(5))];
                });
            }"""
        )
    browser.close()
(source / "outlines.json").write_text(
    json.dumps(outlines, separators=(",", ":")) + "\n", encoding="utf-8"
)
