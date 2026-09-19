"""Real Windows widget input probe, executed by the managed browser Python."""

from __future__ import annotations

import asyncio
import ctypes
import faulthandler
import json
import sys
from ctypes import wintypes
from pathlib import Path


async def main() -> None:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "jarvis" / "society" / "browser"))
    from native_window import NativeWindow
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            sys.argv[2], executable_path=sys.argv[1], headless=False,
            viewport={"width": 1280, "height": 800},
        )
        native = None
        try:
            page = context.pages[0]
            await page.set_content(
                '<input aria-label="Name" style="margin:40px;height:80px;width:400px">'
            )
            cdp = await context.browser.new_browser_cdp_session()
            processes = await cdp.send("SystemInfo.getProcessInfo")
            pid = next(p["id"] for p in processes["processInfo"] if p["type"] == "browser")
            native = NativeWindow(int(pid))
            assert await asyncio.to_thread(native.ready.wait, 5)
            native.park()
            badge = await asyncio.to_thread(native.brand, str(root / "jarvis/assets/icons/jarvis.ico"))
            await asyncio.sleep(0.5)
            u = native.user32
            u.EnumChildWindows.argtypes = [wintypes.HWND, native.callback_type, wintypes.LPARAM]
            children = []

            @native.callback_type
            def child(hwnd, _unused):
                name = ctypes.create_unicode_buffer(128)
                u.GetClassNameW(hwnd, name, len(name))
                if name.value == "Chrome_RenderWidgetHostHWND" and u.IsWindowVisible(hwnd):
                    point, origin = wintypes.POINT(), wintypes.POINT()
                    u.ClientToScreen(hwnd, ctypes.byref(point))
                    u.ClientToScreen(native.hwnd, ctypes.byref(origin))
                    rect = wintypes.RECT()
                    u.GetWindowRect(hwnd, ctypes.byref(rect))
                    children.append((point.x - origin.x, point.y - origin.y, rect.right - rect.left))
                return True

            with native.dpi():
                u.EnumChildWindows(native.hwnd, child, 0)
            assert children
            x, y, width = children[-1]
            scale = width / 1280
            native.input("click", {"x": x + 100 * scale, "y": y + 70 * scale})
            native.input("text", {"text": "Typed through the preview"})
            await asyncio.sleep(0.3)
            value = await page.get_by_label("Name").input_value()
            assert value == "Typed through the preview", value
            assert native.input_hwnd != native.hwnd
            assert badge
            native.input("click", {"x": 300 * scale, "y": y * 0.72})
            native.input("text", {"text": "about:blank#jarvis-input-probe"})
            native.input("key", {"key": "Enter"})
            await page.wait_for_url("about:blank#jarvis-input-probe", timeout=5000)
            print(json.dumps({"typed": value, "badge": badge, "address": page.url}))
        finally:
            if native:
                native.close()
            await context.close()


if __name__ == "__main__":
    faulthandler.enable()
    asyncio.run(main())
