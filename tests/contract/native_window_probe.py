"""Real Windows widget input probe, executed by the managed browser Python."""

from __future__ import annotations

import asyncio
import ctypes
import faulthandler
import json
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


async def capture_styles() -> None:
    """Report capture support for individual window styles on a CI desktop."""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "jarvis" / "society" / "browser"))
    from native_window import NativeWindow
    from playwright.async_api import async_playwright

    original = NativeWindow._hide_from_desktop
    original_park = NativeWindow.park
    variants = [
        ("unchanged", 0, 0, None, False),
        ("background-only", 0, 0, None, True),
        ("no-activate", 0x08000000, 0x40000, None, False),
        ("layered-opaque", 0x80000, 0, 255, False),
        ("layered-minimum", 0x80000, 0, 1, False),
        ("parked-minimum", 0x08080000, 0x40080, 1, False),
        ("positioned-minimum", 0x08080000, 0x40080, 1, True),
        ("parked-click-through", 0x08080020, 0x40080, 1, False),
        ("layered-zero", 0x80000, 0, 0, False),
    ]
    try:
        async with async_playwright() as pw:
            for label, add, remove, alpha, reposition in variants:
                def apply_style(self, hwnd, add=add, remove=remove, alpha=alpha):
                    if not self._owned(hwnd):
                        raise RuntimeError("Probe window ownership changed")
                    u = self.user32
                    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                    u.GetWindowLongW.restype = ctypes.c_long
                    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
                    u.SetWindowLongW.restype = ctypes.c_long
                    style = u.GetWindowLongW(hwnd, -20)
                    wanted = (style | add) & ~remove
                    if wanted != style:
                        u.SetWindowLongW(hwnd, -20, wanted)
                    if alpha is not None:
                        u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
                        u.SetLayeredWindowAttributes.restype = wintypes.BOOL
                        if not u.SetLayeredWindowAttributes(hwnd, 0, alpha, 2):
                            raise RuntimeError("Probe opacity was refused")

                NativeWindow._hide_from_desktop = apply_style
                def styles_only(self):
                    self._hide_from_desktop(self.hwnd)
                    self._parked = True

                NativeWindow.park = original_park if reposition else styles_only
                with tempfile.TemporaryDirectory(prefix="jarvis-capture-style-") as profile:
                    context = await pw.chromium.launch_persistent_context(
                        profile, executable_path=sys.argv[1], headless=False, no_viewport=True,
                    )
                    native = None
                    try:
                        page = context.pages[0]
                        await page.set_content('<h1 style="background:#f00">Capture fixture</h1>')
                        cdp = await context.browser.new_browser_cdp_session()
                        processes = await cdp.send("SystemInfo.getProcessInfo")
                        pid = next(p["id"] for p in processes["processInfo"] if p["type"] == "browser")
                        native = NativeWindow(int(pid))
                        ready = await asyncio.to_thread(native.ready.wait, 5)
                        await asyncio.sleep(0.2)
                        result = {"ready": ready, "failed": native.failed}
                        if ready and not native.failed:
                            result["frame"] = bool(native.frame())
                        print(json.dumps({"capture_style": label, **result}), flush=True)
                    except Exception as exc:
                        print(json.dumps({"capture_style": label, "error": str(exc)}), flush=True)
                    finally:
                        if native:
                            native.close()
                        await context.close()
    finally:
        NativeWindow._hide_from_desktop = original
        NativeWindow.park = original_park


async def main() -> None:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "jarvis" / "society" / "browser"))
    from native_window import NativeWindow
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            # No emulated viewport: the page then fills the real widget, whose
            # size follows the window that park() fits to the work area.
            sys.argv[2], executable_path=sys.argv[1], headless=False, no_viewport=True,
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
            # Widget pixels per CSS pixel, measured instead of assuming a width.
            scale = width / await page.evaluate("window.innerWidth")
            box = await page.get_by_label("Name").bounding_box()
            assert box is not None
            native.input("click", {
                "x": x + (box["x"] + box["width"] / 2) * scale,
                "y": y + (box["y"] + box["height"] / 2) * scale,
            })
            native.input("text", {"text": "Typed through the preview"})
            await asyncio.sleep(0.3)
            value = await page.get_by_label("Name").input_value()
            assert value == "Typed through the preview", value
            assert native.input_hwnd != native.hwnd
            assert badge
            # Focus the address bar through Chrome's own location command, not
            # a click at a guessed toolbar position: on a slow runner the URL
            # never reached the omnibox within the wait (CI run 37599288714).
            # The command and the characters go to the same window queue, so
            # the omnibox owns focus before the first character arrives.
            native.input("key", {"key": "Control+l"})
            native.input("text", {"text": "about:blank#jarvis-input-probe"})
            native.input("key", {"key": "Enter"})
            await page.wait_for_url("about:blank#jarvis-input-probe", timeout=20_000)
            print(json.dumps({"typed": value, "badge": badge, "address": page.url}))
        finally:
            if native:
                native.close()
            await context.close()


if __name__ == "__main__":
    faulthandler.enable()
    asyncio.run(capture_styles() if "--capture-styles" in sys.argv else main())
