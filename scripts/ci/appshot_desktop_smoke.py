#!/usr/bin/env python3
"""Exercise the appshot feature on a REAL desktop session and read the results back.

Unit tests fake the OS; this script does not. It copies pictures and text to
the real clipboard and reads them back with the OS's own tools, burns markings
into a picture, keeps an appshot in the gallery, saves one to Downloads, and
takes a real appshot through the same service the shortcuts use.

Run it only where a desktop session exists that may be clobbered (a CI
runner, Xvfb, a headless Wayland compositor, a container): it REPLACES the
clipboard. Data and config go to a temporary folder, never the user's.

    python scripts/ci/appshot_desktop_smoke.py --report out/appshot-smoke.json
    python scripts/ci/appshot_desktop_smoke.py --expect-capture   # capture must work
    python scripts/ci/appshot_desktop_smoke.py --headless         # no display: degrade

Exit code 1 when a check that must pass on this host failed.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

# Isolate data + config BEFORE anything imports jarvis.core.config.
_SANDBOX = Path(tempfile.mkdtemp(prefix="appshot-smoke-"))
os.environ.setdefault("JARVIS_DATA_DIR", str(_SANDBOX / "data"))
_CONFIG = _SANDBOX / "jarvis.toml"
_CONFIG.write_text("[screen_context]\nenabled = true\n", encoding="utf-8")
os.environ.setdefault("JARVIS_CONFIG", str(_CONFIG))

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TIMEOUT_S = 10.0


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "", *, required: bool = True) -> None:
        self.checks.append({"name": name, "ok": ok, "required": required, "detail": detail})
        mark = "PASS" if ok else ("FAIL" if required else "info")
        print(f"[{mark}] {name}{': ' + detail if detail else ''}", flush=True)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["required"] and not c["ok"]]


def _png(alpha: bool = True) -> bytes:
    from PIL import Image

    image = Image.new("RGBA", (64, 48), (0, 0, 0, 0) if alpha else (255, 255, 255, 255))
    for x in range(8, 56):
        for y in range(8, 40):
            image.putpixel((x, y), (30, 140, 230, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _run(command: list[str], data: bytes | None = None) -> tuple[int, bytes]:
    """Run a READER command (it exits on its own); ``(returncode, stdout)``."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed OS reader commands
            command, input=data, capture_output=True, timeout=_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc).encode()
    return done.returncode, done.stdout


def _session() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


# -- reading the clipboard back with the OS's own tools ----------------------

_MACOS_READ = """
function run(argv) {
  ObjC.import('AppKit');
  var board = $.NSPasteboard.generalPasteboard;
  var types = ObjC.deepUnwrap(board.types) || [];
  var data = board.dataForType('public.png');
  if (!data.isNil()) { data.writeToFileAtomically(argv[0], true); }
  var tiff = board.dataForType('public.tiff');
  return JSON.stringify({types: types, tiff: tiff.isNil() ? 0 : tiff.length});
}
"""


def read_back_image() -> tuple[set[str], bytes | None, str]:
    """``(formats offered, png bytes or None, note)`` as another app would see them."""
    session = _session()
    if session == "windows":
        import ctypes
        from ctypes import wintypes

        from PIL import ImageGrab

        user32 = ctypes.WinDLL("user32")
        user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
        user32.EnumClipboardFormats.restype = wintypes.UINT
        user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
        names: set[str] = set()
        if user32.OpenClipboard(None):
            try:
                fmt = 0
                while fmt := user32.EnumClipboardFormats(fmt):
                    buf = ctypes.create_unicode_buffer(64)
                    user32.GetClipboardFormatNameW(fmt, buf, 64)
                    names.add(
                        buf.value
                        or {2: "CF_BITMAP", 8: "CF_DIB", 17: "CF_DIBV5"}.get(fmt, str(fmt))
                    )
            finally:
                user32.CloseClipboard()
        grabbed = ImageGrab.grabclipboard()
        out = io.BytesIO()
        if grabbed is not None and hasattr(grabbed, "save"):
            grabbed.save(out, "PNG")
        return names, out.getvalue() or None, f"pillow read {getattr(grabbed, 'size', None)}"
    if session == "macos":
        target = _SANDBOX / "pasteboard.png"
        code, out = _run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _MACOS_READ, str(target)])
        if code != 0:
            return set(), None, f"osascript exit {code}: {out[:200]!r}"
        info = json.loads(out.decode("utf-8", "replace") or "{}")
        png = target.read_bytes() if target.exists() else None
        return set(info.get("types", [])), png, f"tiff bytes {info.get('tiff')}"
    if session == "wayland":
        code, out = _run(["wl-paste", "--list-types"])
        types = set(out.decode("utf-8", "replace").split()) if code == 0 else set()
        code, png = _run(["wl-paste", "--no-newline", "--type", "image/png"])
        return types, png if code == 0 else None, "wl-paste"
    if session == "x11":
        code, out = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
        types = set(out.decode("utf-8", "replace").split()) if code == 0 else set()
        code, png = _run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
        return types, png if code == 0 else None, "xclip"
    return set(), None, "no display"


def _same_pixels(a: bytes, b: bytes | None) -> bool:
    if not b:
        return False
    from PIL import Image

    with Image.open(io.BytesIO(a)) as one, Image.open(io.BytesIO(b)) as two:
        return one.convert("RGBA").tobytes() == two.convert("RGBA").tobytes()


EXPECTED_FORMATS = {
    "windows": {"PNG", "CF_DIB"},
    "macos": {"public.png", "public.tiff"},
    "x11": {"image/png"},
    "wayland": {"image/png"},
}


# -- checks --------------------------------------------------------------------


def check_image_clipboard(report: Report) -> None:
    from jarvis.platform.clipboard_image import copy_image

    session = _session()
    png = _png()
    started = time.monotonic()
    result = copy_image(png)
    took = time.monotonic() - started
    if session == "headless":
        report.add(
            "image copy degrades without a display", result.reason == "no_display", result.reason
        )
        return
    report.add(
        "image copy accepted by the OS", result.ok, f"{result.formats} reason={result.reason!r}"
    )
    report.add("image copy returns promptly", took < 3.0, f"{took:.2f}s")
    formats, pasted, note = read_back_image()
    expected = EXPECTED_FORMATS[session]
    report.add(
        "clipboard offers every expected format",
        expected <= formats,
        f"offered={sorted(formats)} expected={sorted(expected)} ({note})",
    )
    report.add("pasted picture equals the copied one", _same_pixels(png, pasted), note)


def check_text_clipboard(report: Report) -> None:
    from jarvis.platform.clipboard import read_text, write_text

    # i18n-allow: umlauts and symbols prove the non-ASCII clipboard round trip.
    text = f"Appshot text check — Größe ✓ {uuid.uuid4().hex[:6]}"  # i18n-allow
    if _session() == "headless":
        report.add("text copy degrades without a display", write_text(text) is False)
        return
    started = time.monotonic()
    ok = write_text(text)
    took = time.monotonic() - started
    report.add("text copy accepted by the OS", ok)
    report.add("text copy returns promptly", took < 3.0, f"{took:.2f}s")
    report.add("text reads back unchanged", read_text() == text)
    if _session() == "macos":
        # An app started from Finder or the Dock has no locale variables.
        saved = {k: os.environ.pop(k) for k in ("LANG", "LC_ALL", "LC_CTYPE") if k in os.environ}
        try:
            write_text(text)
            report.add("text survives an app started without a locale", read_text() == text)
        finally:
            os.environ.update(saved)


def check_jarvisx_copy(report: Report) -> None:
    from jarvis.jarvisx.clipboard import copy_png

    ok, message = copy_png(_png(alpha=False))
    expected = _session() != "headless"
    report.add("Jarvis X copy", ok is expected, message)


def _appshot(png: bytes):
    from jarvis.appshot.store import Appshot

    return Appshot(
        id=uuid.uuid4().hex,
        image=png,
        mime="image/png",
        width=64,
        height=48,
        label="selected area",
        app_name="Smoke",
        note="APPSHOT: smoke",
        ui_text="smoke text",
        trigger="button",
        taken_at=time.time(),
    )


def check_markup_burn(report: Report) -> None:
    import base64

    from PIL import Image

    from jarvis.appshot import markup

    overlay = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
    for x in range(40, 60):
        overlay.putpixel((x, 44), (255, 0, 0, 255))
    buffer = io.BytesIO()
    overlay.save(buffer, "PNG")
    payload = {
        "overlay": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "hides": [{"kind": "pixelate", "rect": [0.1, 0.1, 0.5, 0.5]}],
    }
    parsed = markup.parse_markup(payload)
    report.add("markings payload parses", parsed is not None and len(parsed.hides) == 1)
    if parsed is None:
        return
    burnt = markup.apply_to_bytes(_png(alpha=False), "image/png", parsed)
    with Image.open(io.BytesIO(burnt)) as picture:
        red = picture.convert("RGB").getpixel((50, 44))
    report.add("markings burn into the picture", red[0] > 200 and red[1] < 60, f"pixel {red}")


def check_library_and_downloads(report: Report) -> None:
    from jarvis.appshot import library
    from jarvis.appshot.card_actions import save_shot_to_downloads

    shot = _appshot(_png(alpha=False))
    report.add("gallery keeps the appshot", library.save(shot) is True)
    kept = list((library.library_root()).rglob("*.png"))
    report.add("gallery file is on disk", bool(kept), str(library.library_root()))
    folder = _SANDBOX / "Downloads"
    folder.mkdir(exist_ok=True)
    path = save_shot_to_downloads(shot, folder=folder)
    report.add(
        "Save writes a PNG", path.exists() and path.read_bytes().startswith(b"\x89PNG"), path.name
    )


def check_real_appshot(report: Report, *, expect_capture: bool) -> None:
    from jarvis.appshot import service

    async def no_library(_shot, _config) -> None:
        return None

    service._keep_in_library = no_library  # noqa: SLF001 - keep the sandbox gallery clean
    from jarvis.platform.clipboard import write_text

    write_text("before-appshot")

    async def take():
        result = await service.take_appshot(trigger="button", deliver=False)
        while service._COPIES:  # noqa: SLF001
            await asyncio.gather(*service._COPIES)  # noqa: SLF001
        return result

    try:
        result = asyncio.run(asyncio.wait_for(take(), timeout=60))
    except Exception as exc:  # noqa: BLE001 - the report says what broke
        report.add("real appshot never raises", False, f"{type(exc).__name__}: {exc}")
        return
    report.add("real appshot never raises", True)
    detail = f"status={result.status} reason={result.reason_code!r} message={result.message!r}"
    if result.ok:
        shot = result.shot
        report.add(
            "real appshot captured", True, f"{shot.width}x{shot.height} {shot.label} {detail}"
        )
        formats, pasted, note = read_back_image()
        report.add(
            "real appshot landed on the clipboard",
            bool(pasted) and EXPECTED_FORMATS.get(_session(), set()) <= formats,
            f"offered={sorted(formats)} ({note})",
        )
    else:
        report.add("real appshot captured", False, detail, required=expect_capture)
        report.add("a refusal explains itself", bool(result.message), result.message)


def check_shift_digit_shortcut(report: Report) -> None:
    """X11 only: a real Ctrl+Shift+9 (the recording shortcut) reaches the hotkey backend."""
    if _session() != "x11" or not shutil.which("xdotool"):
        return
    try:
        from jarvis.trigger.backends.pynput import PynputBackend
    except ImportError as exc:
        report.add("Ctrl+Shift+9 fires on X11", False, f"pynput backend unavailable: {exc}")
        return
    for layout in ("us", "de"):
        if shutil.which("setxkbmap"):
            _run(["setxkbmap", layout])
        fired: list[int] = []
        backend = PynputBackend()
        backend.register([("ctrl+shift+9", lambda hits=fired: hits.append(1))])
        backend.start()
        time.sleep(1.0)
        _run(["xdotool", "keydown", "ctrl", "keydown", "shift", "key", "9"])
        _run(["xdotool", "keyup", "shift", "keyup", "ctrl"])
        time.sleep(1.0)
        backend.stop()
        report.add(f"Ctrl+Shift+9 fires on X11 ({layout} layout)", bool(fired))


def check_both_shift_gesture(report: Report) -> None:
    """X11 only: pressing both Shift keys (the area shortcut) fires the watcher."""
    if _session() != "x11" or not shutil.which("xdotool"):
        return
    from jarvis.appshot.gesture import BothKeysWatcher, make_probe, together_window

    probe, reason = make_probe("shift")
    if probe is None:
        report.add("both-Shift shortcut fires on X11", False, reason)
        return
    fired: list[int] = []
    watcher = BothKeysWatcher(
        lambda: fired.append(1), probe=probe, together_s=together_window("shift")
    )
    watcher.start()
    time.sleep(0.5)
    _run(["xdotool", "keydown", "Shift_L", "keydown", "Shift_R"])
    time.sleep(0.5)
    _run(["xdotool", "keyup", "Shift_R", "keyup", "Shift_L"])
    time.sleep(0.3)
    watcher.stop()
    report.add("both-Shift shortcut fires on X11", fired == [1], f"fired {len(fired)}x")


def check_gtk_shadow_trim(report: Report) -> None:
    """X11 only: a window publishing GTK shadow margins is captured without them."""
    if _session() != "x11" or not shutil.which("xdotool"):
        return
    try:
        from Xlib import X, Xatom, display
    except ImportError as exc:
        report.add("GTK shadow margins are trimmed", False, f"python-xlib missing: {exc}")
        return
    from jarvis.platform.window_state import WindowInfo, window_frame_rect

    conn = display.Display()
    root = conn.screen().root
    window = root.create_window(200, 150, 846, 646, 0, conn.screen().root_depth, X.InputOutput)
    window.change_property(
        conn.intern_atom("_GTK_FRAME_EXTENTS"), Xatom.CARDINAL, 32, [23, 23, 15, 31]
    )
    window.map()
    conn.sync()
    time.sleep(0.3)
    try:
        rect = window_frame_rect(WindowInfo(title="csd", handle=window.id))
    finally:
        window.destroy()
        conn.close()
    report.add("GTK shadow margins are trimmed", rect == (223, 165, 800, 600), f"rect {rect}")


def check_macos_gesture_permission(report: Report) -> None:
    """macOS: a both-keys shortcut never reports armed without Input Monitoring."""
    if _session() != "macos":
        return
    from jarvis.appshot.gesture import make_probe
    from jarvis.platform.permissions import PermissionId, get_system_permission_port

    granted = get_system_permission_port().runtime_access_granted(PermissionId.INPUT_MONITORING)
    probe, reason = make_probe("alt")
    if granted:
        report.add("both-Option shortcut arms with Input Monitoring", probe is not None, reason)
    else:
        report.add(
            "both-Option shortcut names the missing Input Monitoring grant",
            probe is None and "Input Monitoring" in reason,
            reason,
        )


def check_headless_imports(report: Report) -> None:
    for module in (
        "jarvis.appshot.service",
        "jarvis.appshot.card_actions",
        "jarvis.appshot.library",
        "jarvis.ui.web.appshot_routes",
        "jarvis.jarvisx.service",
        "jarvis.platform.clipboard_image",
    ):
        try:
            __import__(module)
            report.add(f"imports without a display: {module}", True)
        except Exception as exc:  # noqa: BLE001
            report.add(
                f"imports without a display: {module}", False, f"{type(exc).__name__}: {exc}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--expect-capture", action="store_true")
    parser.add_argument("--headless", action="store_true", help="assert the no-display path")
    args = parser.parse_args()

    report = Report()
    session = _session()
    host = f"{platform.platform()} / Python {platform.python_version()}"
    print(f"appshot smoke on {host} / session={session}")
    if args.headless and session != "headless":
        report.add("headless run has no display", False, session)
    steps = [
        check_headless_imports,
        check_image_clipboard,
        check_text_clipboard,
        check_jarvisx_copy,
    ]
    steps += [check_markup_burn, check_library_and_downloads, check_shift_digit_shortcut]
    steps += [check_macos_gesture_permission, check_both_shift_gesture, check_gtk_shadow_trim]
    for step in steps:
        try:
            step(report)
        except Exception as exc:  # noqa: BLE001 - one broken check never hides the rest
            report.add(step.__name__, False, "".join(traceback.format_exception_only(exc)).strip())
    if session != "headless":
        check_real_appshot(report, expect_capture=args.expect_capture)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {"session": session, "platform": platform.platform(), "checks": report.checks},
                indent=2,
            ),
            encoding="utf-8",
        )
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    failed = report.failed
    passed = len(report.checks) - len(failed)
    print(f"\n{passed}/{len(report.checks)} checks ok; {len(failed)} required failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
