"""Screenshot app views in an isolated headless Chrome for design review.

Drives Chrome over the DevTools protocol against a running app instance (the
live one on 127.0.0.1:47821 by default). Look and layout are seeded through
localStorage in a THROWAWAY profile, so the maintainer's own app state is never
touched. Recent chats are hidden (they list real conversations).

    python scripts/design_screenshots.py --out docs/design/redesign --prefix phase-1 \\
        --views chats,local-models --themes dark,light

Every path handed to Chrome must be a native Windows path on Windows.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME_CANDIDATES = [
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
]

HIDE_CSS = '[data-testid="recent-chats"] { display: none !important; }'

SEED = {
    "jarvis.background.v1": "solid",
    "jarvis.sidebar.collapsed.v1": "0",
    "jarvis.sidebar.width.v4": "240",
}


def find_chrome() -> Path:
    for c in CHROME_CANDIDATES:
        if c.exists():
            return c
    found = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return Path(found)
    raise SystemExit("no Chrome binary found")


def launch(args: argparse.Namespace, profile: Path) -> tuple[subprocess.Popen, str]:
    """Start headless Chrome and return the process plus its page's DevTools URL."""
    proc = subprocess.Popen(  # noqa: S603 — fixed argv
        [
            str(find_chrome()),
            "--headless=new",
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile}",
            f"--window-size={args.width},{args.height}",
            "--hide-scrollbars",
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            "--no-first-run",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/json") as r:  # noqa: S310
                tabs = json.load(r)
            page = next(t for t in tabs if t["type"] == "page")
            return proc, page["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001 — Chrome is still starting
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("Chrome did not expose a page target")


async def capture(args: argparse.Namespace, ws_url: str) -> list[tuple[str, bytes]]:
    import websockets

    shots: list[tuple[str, bytes]] = []
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        seq = 0

        async def call(method: str, **params):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == seq:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        await call("Page.enable")
        await call("Runtime.enable")
        await call(
            "Emulation.setDeviceMetricsOverride",
            width=args.width,
            height=args.height,
            deviceScaleFactor=1,
            mobile=False,
        )
        for theme in args.themes.split(","):
            await call("Page.navigate", url=args.base)
            await asyncio.sleep(1.0)
            seed = {**SEED, "jarvis.theme": theme}
            js = "".join(
                f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
                for k, v in seed.items()
            )
            await call("Runtime.evaluate", expression=js)
            for view in args.views.split(","):
                await call("Page.navigate", url=f"{args.base}?view={view}")
                await asyncio.sleep(args.settle)
                await call(
                    "Runtime.evaluate",
                    expression=(
                        "(function(){var s=document.createElement('style');"
                        f"s.textContent={json.dumps(HIDE_CSS)};document.head.appendChild(s);"
                        "return document.documentElement.className;})()"
                    ),
                )
                await asyncio.sleep(0.4)
                shot = await call("Page.captureScreenshot", format="png")
                shots.append((f"{args.prefix}-{view}-{theme}.png", base64.b64decode(shot["data"])))
    return shots


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:47821/")
    ap.add_argument("--out", default="docs/design/redesign")
    ap.add_argument("--prefix", default="shot")
    ap.add_argument("--views", default="chats")
    ap.add_argument("--themes", default="dark")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--settle", type=float, default=5.0)
    ap.add_argument("--port", type=int, default=9333)
    args = ap.parse_args()

    profile = Path(tempfile.mkdtemp(prefix="jarvis-shots-"))
    proc, ws_url = launch(args, profile)
    try:
        shots = asyncio.run(capture(args, ws_url))
    finally:
        proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in shots:
        target = out / name
        target.write_bytes(data)
        print(target, file=sys.stderr)


if __name__ == "__main__":
    main()
