"""Capture Obsidian via PrintWindow API.

PrintWindow with PW_RENDERFULLCONTENT (0x2) captures a window's pixel
content regardless of z-order or foreground state. This bypasses the
focus-stealing problem we hit when capturing via pyautogui.screenshot()
from a OpenClaw session.

Flow:
  1. Kill all Obsidian instances.
  2. Wipe Obsidian's chromium storage so it does NOT auto-open the
     user's other vault.
  3. Write a solo obsidian.json with only our vault.
  4. Launch Obsidian; wait for the vault picker.
  5. Send Alt+O to focus the German-localized 'Öffnen' (Open) button via pywinauto's PostMessage,  # i18n-allow: quotes the real button label of Obsidian's German-localized UI
     then dispatch a click on the Obsidian HWND directly.

Honestly the cleanest path is: register the vault path in obsidian.json
with open=true, wipe state, and rely on Obsidian to auto-load it on
launch. If that works, no clicking is needed.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT_PATH = REPO_ROOT / "wiki" / "obsidian-vault"
SHOT_DIR = REPO_ROOT / "docs" / "screenshots"
OBSIDIAN_EXE = Path(r"C:\Program Files\Obsidian\Obsidian.exe")
OBSIDIAN_APPDATA = Path(os.environ["APPDATA"]) / "obsidian"


def log(msg: str) -> None:
    print(f"[pw-cap] {msg}", flush=True)


def kill_obsidian() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "Obsidian.exe"],
        capture_output=True,
        check=False,
    )
    time.sleep(2)


def write_solo_registry() -> None:
    """Replace obsidian.json with a single registered vault entry."""
    for sub in ("Local Storage", "Session Storage", "IndexedDB"):
        shutil.rmtree(OBSIDIAN_APPDATA / sub, ignore_errors=True)
    payload = {
        "vaults": {
            "a1b2c3d4e5f60718": {
                "path": str(VAULT_PATH),
                "ts": int(time.time() * 1000),
                "open": True,
            }
        }
    }
    (OBSIDIAN_APPDATA / "obsidian.json").write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )
    log("Solo registry written")


def find_obsidian_hwnds() -> list[int]:
    import psutil
    import win32gui
    import win32process

    result: list[int] = []

    def cb(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            if psutil.Process(pid).name().lower() == "obsidian.exe":
                title = win32gui.GetWindowText(hwnd)
                if title:
                    result.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return result


def capture_hwnd_to_png(hwnd: int, out_path: Path) -> bool:
    """Capture a HWND's pixels via PrintWindow + GDI bitmap → PNG."""
    import win32gui
    import win32ui
    import win32con
    from PIL import Image

    rect = win32gui.GetWindowRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if width <= 0 or height <= 0:
        log(f"Invalid hwnd rect: {rect}")
        return False

    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    saveBitMap = win32ui.CreateBitmap()
    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
    saveDC.SelectObject(saveBitMap)

    PW_RENDERFULLCONTENT = 0x00000002
    result = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
    if not result:
        log(f"PrintWindow returned 0 for hwnd {hwnd}")

    bmpinfo = saveBitMap.GetInfo()
    bmpstr = saveBitMap.GetBitmapBits(True)
    im = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr,
        "raw",
        "BGRX",
        0,
        1,
    )
    im.save(out_path)
    win32gui.DeleteObject(saveBitMap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)
    log(f"Saved {out_path}")
    return True


def send_keys_to_hwnd(hwnd: int, vk_codes: list[int]) -> None:
    """Send a sequence of virtual-key keypresses to a specific HWND via PostMessage."""
    import win32con
    import win32api
    for vk in vk_codes:
        ctypes.windll.user32.PostMessageW(hwnd, win32con.WM_KEYDOWN, vk, 0)
        time.sleep(0.05)
        ctypes.windll.user32.PostMessageW(hwnd, win32con.WM_KEYUP, vk, 0)
        time.sleep(0.05)


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    kill_obsidian()
    write_solo_registry()

    log("Launching Obsidian...")
    subprocess.Popen([str(OBSIDIAN_EXE)])
    time.sleep(15)

    hwnds = find_obsidian_hwnds()
    if not hwnds:
        log("No Obsidian window found")
        return 1
    import win32gui
    for h in hwnds:
        log(f"HWND {h}: '{win32gui.GetWindowText(h)}'")
    main_hwnd = hwnds[0]

    # Output filename comes from CLI arg, default to overview
    out_name = sys.argv[1] if len(sys.argv) > 1 else "obsidian-vault-overview.png"
    capture_hwnd_to_png(main_hwnd, SHOT_DIR / out_name)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
