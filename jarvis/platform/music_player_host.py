"""Companion process hosting the background music player window.

``python -m jarvis.platform.music_player_host --storage <dir> --title <title>``

A pywebview window that starts MINIMIZED (a taskbar entry, no focus, no tab —
and unlike a truly hidden window, WebView2 does start media in it), keeps its
own persistent browser profile (so a YouTube Music login survives restarts),
and takes JSON-line commands on stdin — ``load`` a URL, ``show`` / ``hide`` the window, read the
player ``state``, ``pause`` / ``play`` / ``toggle`` / ``next`` / ``previous`` /
``volume`` — answering one JSON line per command on stdout. That closed list
IS the protocol: no free-form script ever crosses the pipe. Playback therefore happens
in the background: no browser tab, no focus steal, one window that navigates
instead of piling up tabs.

Why its own process rather than a window in the desktop shell: pywebview
holds one browser profile per process, and the shell runs in private mode on
purpose. This host runs a second, persistent profile without touching that,
works the same from ``--headless`` mode as long as a display exists, and dies
with its parent — stdin EOF is the lifeline, so a crashed Jarvis never leaves a
ghost player behind.

Autoplay: a hidden window never sees a user gesture, so on Windows the
WebView2 environment is asked to allow autoplay outright (the documented
``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` override, set before the runtime is
created). Other backends fall back to reporting "not playing", which the tool
turns into showing the window with an honest "press play once".

Never imported by Jarvis proper (HN-7): the parent talks to it over pipes via
``jarvis.platform.music_player``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# What the tool needs from the page: the media-session metadata YouTube Music
# publishes plus the <video> element's transport state. Pure DOM, no login.
_STATE_JS = """
(() => {
  const v = document.querySelector('video');
  const m = (navigator.mediaSession && navigator.mediaSession.metadata) || null;
  const t = document.title || '';
  return JSON.stringify({
    url: location.href,
    title: m ? m.title : (t.replace(/\\s*[|\\-]\\s*YouTube Music\\s*$/, '') || ''),
    artist: m ? m.artist : '',
    album: m ? m.album : '',
    has_video: !!v,
    paused: v ? v.paused : null,
    ended: v ? v.ended : null,
    position: v ? v.currentTime : null,
    duration: v && isFinite(v.duration) ? v.duration : null,
    volume: v ? Math.round(v.volume * 100) : null,
    ready: !!document.querySelector('ytmusic-player-bar'),
    consent: /consent\\.(youtube|google)\\./.test(location.hostname),
    signed_in: !!document.querySelector(
      'ytmusic-settings-button, ytmusic-nav-bar #right-content img'),
  });
})()
"""

# YouTube Music's player-bar buttons. The <video> element itself is the
# reliable play/pause surface; next/previous only exist as buttons.
_JS = {
    "play": (
        "(() => { const v = document.querySelector('video'); if (!v) return false; "
        "v.play(); return true; })()"
    ),
    "pause": (
        "(() => { const v = document.querySelector('video'); if (!v) return false; "
        "v.pause(); return true; })()"
    ),
    "toggle": (
        "(() => { const v = document.querySelector('video'); if (!v) return false; "
        "if (v.paused) v.play(); else v.pause(); return true; })()"
    ),
    "next": (
        "(() => { const b = document.querySelector('ytmusic-player-bar .next-button, "
        "ytmusic-player-bar tp-yt-paper-icon-button.next-button, .next-button'); "
        "if (!b) return false; b.click(); return true; })()"
    ),
    "previous": (
        "(() => { const b = document.querySelector('ytmusic-player-bar .previous-button, "
        "ytmusic-player-bar tp-yt-paper-icon-button.previous-button, .previous-button'); "
        "if (!b) return false; b.click(); return true; })()"
    ),
}


def _volume_js(level: int) -> str:
    frac = max(0, min(int(level), 100)) / 100.0
    # Drive YouTube Music's own slider when it is there (it re-applies its
    # value on every track change), and the element directly as the fallback.
    return (
        "(() => { const v = document.querySelector('video'); "
        "const s = document.querySelector('ytmusic-player-bar #volume-slider'); "
        f"if (s) {{ s.value = {int(frac * 100)}; "
        "s.dispatchEvent(new CustomEvent('change', {bubbles: true})); } "
        f"if (v) v.volume = {frac}; return !!(v || s); }})()"
    )


def _log(text: str) -> None:
    """Diagnostics go to stderr; stdout is the protocol channel."""
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _serve(window: Any) -> None:
    """Command loop on a worker thread — every window call marshals to the UI
    thread inside pywebview, so this thread only ever blocks on stdin."""
    _emit({"event": "ready"})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _emit({"id": None, "ok": False, "error": "bad json"})
            continue
        req_id = msg.get("id")
        cmd = str(msg.get("cmd") or "")
        try:
            result = _dispatch(window, cmd, msg)
        except Exception as exc:  # noqa: BLE001 — one bad command must not kill the player
            _emit({"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if cmd == "quit":
            _emit({"id": req_id, "ok": True})
            break
        _emit({"id": req_id, "ok": True, "result": result})
    # stdin closed (parent gone or asked to quit): the caller takes the window
    # down so webview.start() returns and the process ends.


#: How long a page-reading command waits for the current navigation to finish
#: before answering "still loading". pywebview gates ``evaluate_js`` on its
#: ``loaded`` event and blocks up to 20 s when a navigation has not reported
#: back (live 2026-08-22 20:01:52: the second ``load`` of the session left
#: ``loaded`` clear, every ``state`` then sat 20 s in the gate while the parent
#: gave up at 10 s, and one play request took 199 s — 18 reads plus the
#: ``show`` queued behind them). The command loop is sequential, so one blocked
#: read holds every later command hostage; a short bounded wait keeps the loop
#: answering and lets the parent decide what to do with a page that is still
#: turning.
_LOADED_WAIT_S = 1.0

_LOADING_STATE: dict[str, Any] = {
    "loading": True,
    "ready": False,
    "has_video": False,
    "paused": None,
    "ended": None,
    "position": None,
    "duration": None,
    "volume": None,
    "consent": False,
    "signed_in": False,
    "url": "",
    "title": "",
    "artist": "",
    "album": "",
}


def _page_loaded(window: Any, wait_s: float | None = None) -> bool:
    """True when the window's page has finished loading (bounded wait)."""
    if wait_s is None:
        wait_s = _LOADED_WAIT_S
    loaded = getattr(getattr(window, "events", None), "loaded", None)
    if loaded is None:
        return True  # a backend without the event has no gate to wait on
    try:
        if loaded.is_set():
            return True
        return bool(loaded.wait(wait_s))
    except Exception as exc:  # noqa: BLE001 — an odd event object must not block a command
        _log(f"loaded probe skipped: {exc}")
        return True


def _force_foreground(window: Any) -> None:
    """Windows fallback: un-minimize and raise the player window by its title.

    pywebview's ``restore``/``show`` normally do this; when they do not (the
    window stayed iconic on 2026-08-22 while the parent reported it shown),
    the Win32 calls are the ground truth. Capability-gated: a quiet no-op
    anywhere ``ctypes.windll`` does not exist.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        title = str(getattr(window, "title", "") or "")
        if not title:
            return
        handle = user32.FindWindowW(None, title)
        if not handle:
            return
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(handle)
    except Exception as exc:  # noqa: BLE001 — a foreground nudge is best-effort
        _log(f"foreground nudge skipped: {exc}")


def _dispatch(window: Any, cmd: str, msg: dict[str, Any]) -> Any:
    if cmd == "load":
        window.load_url(str(msg.get("url") or "about:blank"))
        return True
    if cmd == "show":
        # Bring the (minimized) player forward for the user to log in, pick a
        # cookie choice, or press play once.
        try:
            window.restore()
        except Exception as exc:  # noqa: BLE001 — not every backend has restore
            _log(f"restore skipped: {exc}")
        window.show()
        _force_foreground(window)
        return True
    if cmd == "hide":
        # Minimize, never hide: WebView2 does not start media in a window that
        # has never been visible (measured 2026-08-18: position stayed at 0
        # while hidden, advanced the moment the window was shown), and a
        # minimized window keeps playing. The taskbar entry is the "background".
        try:
            window.minimize()
        except Exception as exc:  # noqa: BLE001 — a backend without minimize hides instead
            _log(f"minimize skipped, hiding: {exc}")
            window.hide()
        return True
    if cmd == "state":
        if not _page_loaded(window):
            # Answer NOW with "still loading" instead of sitting in pywebview's
            # 20 s gate: the parent polls again, and the next command is not
            # stuck behind this one.
            return dict(_LOADING_STATE)
        raw = window.evaluate_js(_STATE_JS)
        return json.loads(raw) if isinstance(raw, str) else raw
    if cmd == "volume":
        if not _page_loaded(window):
            raise RuntimeError("the player page is still loading")
        return window.evaluate_js(_volume_js(int(msg.get("level") or 0)))
    if cmd in _JS:
        if not _page_loaded(window):
            raise RuntimeError("the player page is still loading")
        return window.evaluate_js(_JS[cmd])
    if cmd == "quit":
        return True
    raise ValueError(f"unknown command {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Background music player host")
    parser.add_argument("--storage", required=True, help="persistent browser profile dir")
    parser.add_argument("--title", default="Music", help="window title")
    parser.add_argument(
        "--icon",
        default="",
        help=(
            "window/taskbar icon file (.ico); without it the pythonw interpreter's "
            "own logo shows"
        ),
    )
    parser.add_argument(
        "--start",
        choices=("minimized", "hidden", "offscreen", "visible"),
        default="minimized",
        help="initial window state (minimized plays from the start; hidden does not)",
    )
    args = parser.parse_args(argv)

    # The protocol is UTF-8 on both pipes whatever the console code page says
    # (a title with an umlaut must survive the round trip on a cp1252 box).
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — a non-reconfigurable stream keeps its encoding
            _log(f"stream reconfigure skipped: {exc}")

    # Must be in the environment before the WebView2 runtime is created.
    flag = "--autoplay-policy=no-user-gesture-required"
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if flag not in existing:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{existing} {flag}".strip()

    try:
        import webview  # noqa: PLC0415 — [desktop] extra, never module-level
    except Exception as exc:  # noqa: BLE001 — the parent reads this line and degrades
        _emit({"event": "unavailable", "error": f"{type(exc).__name__}: {exc}"})
        return 2

    os.makedirs(args.storage, exist_ok=True)
    extra: dict[str, Any] = {}
    if args.start == "hidden":
        extra["hidden"] = True
    elif args.start == "minimized":
        extra["minimized"] = True
    elif args.start == "offscreen":
        extra["x"] = -32000
        extra["y"] = -32000
    window: Any = webview.create_window(
        args.title,
        "about:blank",
        width=1080,
        height=720,
        min_size=(720, 480),
        confirm_close=False,
        **extra,
    )
    if window is None:  # pywebview returns None only when the GUI cannot be created
        _emit({"event": "unavailable", "error": "no window could be created"})
        return 2

    quitting = {"flag": False}

    def _on_closing() -> bool:
        # The user's X minimizes the player instead of ending playback; only
        # the parent's quit (or its death) really closes the window.
        if quitting["flag"]:
            return True
        try:
            window.minimize()
        except Exception as exc:  # noqa: BLE001 — minimizing a dying window is fine
            _log(f"minimize on close skipped: {exc}")
        return False

    window.events.closing += _on_closing

    def _serve_then_quit() -> None:
        # Whatever ends the command loop — EOF, quit, or a read fault — the
        # window must go down, or the GUI loop would keep a ghost player alive.
        try:
            _serve(window)
        except Exception as exc:  # noqa: BLE001 — logged, and the finally still tears down
            _log(f"serve loop ended with {type(exc).__name__}: {exc}")
        finally:
            quitting["flag"] = True
            try:
                window.destroy()
            except Exception as exc:  # noqa: BLE001 — already gone is fine
                _log(f"destroy skipped: {exc}")

    try:
        # pywebview runs ``func`` on its own worker thread once the GUI loop is up.
        # ``icon`` is honoured by the WinForms/GTK/Qt backends; a missing file
        # means the interpreter's default, which is exactly the Python logo the
        # parent passes an icon to avoid.
        icon = args.icon if args.icon and os.path.isfile(args.icon) else None
        webview.start(
            func=_serve_then_quit,
            private_mode=False,
            storage_path=args.storage,
            icon=icon,
        )
    except Exception as exc:  # noqa: BLE001 — no GUI backend here (Linux without GTK/Qt)
        _emit({"event": "unavailable", "error": f"{type(exc).__name__}: {exc}"})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
