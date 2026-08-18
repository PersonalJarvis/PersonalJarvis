"""Companion process hosting the background music player window.

``python -m jarvis.platform.music_player_host --storage <dir> --title <title>``

A pywebview window that starts MINIMIZED (a taskbar entry, no focus, no tab —
and unlike a truly hidden window, WebView2 does start media in it), keeps its
own persistent browser profile (so a YouTube Music login survives restarts),
and takes JSON-line commands on stdin — ``load`` a URL, ``show`` / ``hide`` the window, read the
player ``state``, ``pause`` / ``play`` / ``next`` / ``previous`` / ``volume``
— answering one JSON line per command on stdout. Playback therefore happens
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
        raw = window.evaluate_js(_STATE_JS)
        return json.loads(raw) if isinstance(raw, str) else raw
    if cmd == "eval":
        return window.evaluate_js(str(msg.get("js") or ""))
    if cmd == "volume":
        return window.evaluate_js(_volume_js(int(msg.get("level") or 0)))
    if cmd in _JS:
        return window.evaluate_js(_JS[cmd])
    if cmd == "quit":
        return True
    raise ValueError(f"unknown command {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Background music player host")
    parser.add_argument("--storage", required=True, help="persistent browser profile dir")
    parser.add_argument("--title", default="Music", help="window title")
    parser.add_argument(
        "--start",
        choices=("minimized", "hidden", "offscreen", "visible"),
        default="minimized",
        help="initial window state (minimized plays from the start; hidden does not)",
    )
    args = parser.parse_args(argv)

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
    window = webview.create_window(
        args.title,
        "about:blank",
        width=1080,
        height=720,
        min_size=(720, 480),
        confirm_close=False,
        **extra,
    )

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
        _serve(window)
        quitting["flag"] = True
        try:
            window.destroy()
        except Exception as exc:  # noqa: BLE001 — already gone is fine
            _log(f"destroy skipped: {exc}")

    try:
        # pywebview runs ``func`` on its own worker thread once the GUI loop is up.
        webview.start(func=_serve_then_quit, private_mode=False, storage_path=args.storage)
    except Exception as exc:  # noqa: BLE001 — no GUI backend here (Linux without GTK/Qt)
        _emit({"event": "unavailable", "error": f"{type(exc).__name__}: {exc}"})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
