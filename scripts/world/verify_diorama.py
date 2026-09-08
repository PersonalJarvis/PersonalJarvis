"""Visual acceptance against the isolated Vite world-lab.html surface.

Requires the optional Playwright Python package and an installed Chrome.
Writes screenshots and measurements under .tmp, never touches live agents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


async def verify(url: str, out: Path, full: bool) -> None:
    from playwright.async_api import async_playwright

    errors: list[str] = []
    measurements = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 1600, "height": 1000}, device_scale_factor=1
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.route(
                "**/api/**",
                lambda route: route.fulfill(json={"quests": [], "messages": [], "items": []}),
            )
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_function("window.worldLab?.actors().length === 30", timeout=60000)
            await page.wait_for_function(
                "window.worldLab.diagnostics().frames >= 120", timeout=60000
            )
            views = (
                [(yaw, zoom) for yaw in (45, 135, 225, 315) for zoom in range(5)]
                if full
                else [(45, 1), (45, 4)]
            )
            for yaw, zoom in views:
                await page.evaluate(
                    "([yaw, zoom]) => { worldLab.camera.setState({ yaw, zoom, target: [0,0] }); "
                    "worldLab.resetDiagnostics(); }",
                    [yaw, zoom],
                )
                await page.wait_for_function(
                    "window.worldLab?.diagnostics().frames >= 120", timeout=60000
                )
                metrics = await page.evaluate("worldLab.diagnostics()")
                metrics.update(yaw=yaw, zoom=zoom)
                measurements.append(metrics)
                await page.screenshot(path=str(out / f"world-{yaw}-{zoom}.png"))
            integrity = await page.evaluate("worldLab.integrity()")
            renderer = await page.evaluate("""() => {
                const gl = document.querySelector('canvas').getContext('webgl2');
                const info = gl.getExtension('WEBGL_debug_renderer_info');
                return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : 'WebGL2';
            }""")
            result = {
                "viewport": [1600, 1000],
                "agents": 30,
                "renderer": renderer,
                "measurements": measurements,
                "integrity": integrity,
                "errors": errors,
            }
            (out / "browser-evidence.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result), flush=True)
            if errors or integrity["blocked"] or integrity["overlaps"]:
                raise SystemExit("Diorama visual acceptance failed; inspect browser-evidence.json")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5190/world-lab.html")
    parser.add_argument("--out", type=Path, default=Path(".tmp/pixel-diorama"))
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(verify(args.url, args.out, args.full))
