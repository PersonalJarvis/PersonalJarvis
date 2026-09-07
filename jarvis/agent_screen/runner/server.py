"""Verb handling for the in-session runner, plus both server loops.

The runner deliberately reuses the production actuation and window layers
(``jarvis.cu.actuate``, ``jarvis.platform.window_state``) rather than
re-implementing input. Those backends inject into the input stream of the
process's OWN session — which is precisely why running them here, inside the
isolated session, produces invisible-to-the-user control while the identical
code on the host would grab the user's pointer. The isolation comes from
WHERE the code runs, not from a different code path.

Security posture: the runner answers only on loopback (HTTP transport) or
through a directory the host explicitly mapped (mailbox transport), and every
request must carry the token minted by the host at boot. The token lives in a
file rather than argv so it never appears in a process listing.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from jarvis.agent_screen.wire import (
    BIN_SUFFIX,
    HEADER_FORMAT,
    HEADER_HEIGHT,
    HEADER_TOKEN,
    HEADER_WIDTH,
    MAILBOX_POLL_S,
    PROTOCOL_VERSION,
    REQ_SUFFIX,
    RES_SUFFIX,
    write_atomic,
)

logger = logging.getLogger(__name__)

#: Wheel notches per scroll unit, matching the host tools' vocabulary.
_SCROLL_UNIT = 1


class ScreenServer:
    """Executes the wire verbs against the session this process runs in."""

    def __init__(self, *, kind: str = "xvfb") -> None:
        self.kind = kind
        self._stop = threading.Event()
        self._geometry: tuple[int, int, int, int] | None = None
        # One actuator for the process: building it constructs keycode tables,
        # and the host-side backends already treat it as process-wide state.
        self._actuator: Any = None
        self._actuator_error: str = ""

    # -- helpers -----------------------------------------------------------

    @property
    def stopped(self) -> threading.Event:
        return self._stop

    def _get_actuator(self) -> Any:
        if self._actuator is None and not self._actuator_error:
            try:
                from jarvis.cu.actuate import get_actuator  # noqa: PLC0415

                self._actuator = get_actuator()
            except Exception as exc:  # noqa: BLE001 — reported per action
                self._actuator_error = str(exc)
        if self._actuator is None:
            raise RuntimeError(
                self._actuator_error or "no input backend is available in this screen",
            )
        return self._actuator

    def _screen_rect(self) -> tuple[int, int, int, int]:
        if self._geometry is not None:
            return self._geometry
        import mss  # noqa: PLC0415

        with mss.mss() as sct:
            monitors = sct.monitors
            if not monitors:
                raise RuntimeError("this screen has no framebuffer")
            # ``monitors[0]`` is the union of all displays; an isolated screen
            # has exactly one, so the union IS the screen and stays correct
            # even for an X display configured with several virtual heads.
            box = monitors[0]
            self._geometry = (
                int(box["left"]),
                int(box["top"]),
                int(box["width"]),
                int(box["height"]),
            )
        return self._geometry

    # -- verbs -------------------------------------------------------------

    def health(self, _params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        return ({"ok": True, "v": PROTOCOL_VERSION, "kind": self.kind}, b"")

    def geometry(self, _params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        left, top, width, height = self._screen_rect()
        return (
            {"ok": True, "left": left, "top": top, "width": width, "height": height},
            b"",
        )

    def grab(self, params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        import mss  # noqa: PLC0415

        bbox = {
            "left": int(params.get("left", 0)),
            "top": int(params.get("top", 0)),
            "width": max(1, int(params.get("width", 1))),
            "height": max(1, int(params.get("height", 1))),
        }
        want_rgb = bool(params.get("rgb", False))
        with mss.mss() as sct:
            shot = sct.grab(bbox)
        if want_rgb:
            data = bytes(shot.rgb)
            pixel_format = "RGB"
        else:
            # ``shot.raw`` is the backend's native BGRX buffer. Handing it over
            # untouched saves a full-desktop conversion on every stability
            # re-grab; the host's Pillow decode understands the layout.
            data = bytes(shot.raw)
            pixel_format = "BGRX"
        return (
            {
                "ok": True,
                "binary": True,
                "width": int(shot.size[0]),
                "height": int(shot.size[1]),
                "format": pixel_format,
            },
            data,
        )

    def foreground(self, _params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        try:
            from jarvis.platform import window_state  # noqa: PLC0415

            window = window_state.foreground_window()
            if window is None:
                return ({"ok": True, "available": False}, b"")
            rect = window_state.window_frame_rect(window)
        except Exception:  # noqa: BLE001 — an unreadable foreground is a
            # first-class answer, not a crash: the host fails closed on it.
            logger.debug("[screen-runner] foreground read failed", exc_info=True)
            return ({"ok": True, "available": False}, b"")
        handle = getattr(window, "handle", None)
        return (
            {
                "ok": True,
                "available": True,
                "title": str(getattr(window, "title", "") or ""),
                "app": str(getattr(window, "process_name", "") or ""),
                "handle": int(handle) if handle else None,
                "rect": list(rect) if rect else None,
            },
            b"",
        )

    def ui_snapshot(self, _params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Accessibility labels from inside the screen, when it has a bridge.

        A fresh Windows Sandbox or a bare X display usually has none, and the
        honest ``supported: false`` makes the host ground on pixels alone
        instead of pretending the screen has no buttons.
        """
        try:
            from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

            source = make_ui_tree_source()
            if type(source).__name__ == "NullUITreeSource":
                return ({"ok": True, "supported": False}, b"")
            import asyncio  # noqa: PLC0415

            from jarvis.cu.verify import (  # noqa: PLC0415
                clickable_labels,
                clickable_rects,
                field_values_hint,
                human_handoff_reason,
            )

            observation = asyncio.run(source.observe())
            nodes = tuple(getattr(observation, "nodes", ()) or ())
        except Exception:  # noqa: BLE001 — best-effort enumeration
            logger.debug("[screen-runner] ui snapshot failed", exc_info=True)
            return ({"ok": True, "supported": False}, b"")
        return (
            {
                "ok": True,
                "supported": True,
                "labels": clickable_labels(nodes),
                "field_hint": field_values_hint(nodes),
                "handoff": human_handoff_reason(nodes),
                "clickables": [
                    [name, role, list(bounds)] for name, role, bounds in clickable_rects(nodes)
                ],
            },
            b"",
        )

    def typed_text_landed(self, params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        try:
            import asyncio  # noqa: PLC0415

            from jarvis.cu.verify import typed_text_landed  # noqa: PLC0415
            from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

            source = make_ui_tree_source()
            if type(source).__name__ == "NullUITreeSource":
                return ({"ok": True, "landed": None}, b"")
            observation = asyncio.run(source.observe())
            nodes = tuple(getattr(observation, "nodes", ()) or ())
            verdict = typed_text_landed(nodes, str(params.get("text", "")))
        except Exception:  # noqa: BLE001
            logger.debug("[screen-runner] type read-back failed", exc_info=True)
            return ({"ok": True, "landed": None}, b"")
        return ({"ok": True, "landed": verdict}, b"")

    def click_landed_in_focus(
        self,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        try:
            import asyncio  # noqa: PLC0415

            from jarvis.cu.verify import verify_click_focus_point  # noqa: PLC0415

            area = int(params.get("capture_area", 0) or 0)
            verdict = asyncio.run(
                verify_click_focus_point(
                    int(params.get("x", 0)),
                    int(params.get("y", 0)),
                    capture_area=area or None,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.debug("[screen-runner] focus hit-test failed", exc_info=True)
            return ({"ok": True, "focused": None}, b"")
        return ({"ok": True, "focused": verdict}, b"")

    def act(self, params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        action = str(params.get("action", ""))
        args = dict(params.get("params") or {})
        try:
            detail = self._dispatch_action(action, args)
        except Exception as exc:  # noqa: BLE001 — every failure is reported to
            # the host as a failed ACTION, never as a transport error: the
            # engine's verify/retry ladder is built to consume exactly that.
            logger.debug("[screen-runner] action %s failed", action, exc_info=True)
            return ({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, b"")
        return ({"ok": True, "detail": detail}, b"")

    def _dispatch_action(self, action: str, args: dict[str, Any]) -> str:
        from jarvis.cu.actuate import (  # noqa: PLC0415
            verified_click,
            verified_drag,
        )

        if action == "click":
            actuator = self._get_actuator()
            x, y = int(args["x"]), int(args["y"])
            outcome = verified_click(
                actuator,
                x,
                y,
                button=str(args.get("button", "left")),
                double=bool(args.get("double", False)),
            )
            if not outcome.ok:
                raise RuntimeError(outcome.detail)
            return f"click at ({x},{y})"

        if action == "drag":
            actuator = self._get_actuator()
            outcome = verified_drag(
                actuator,
                int(args["x1"]),
                int(args["y1"]),
                int(args["x2"]),
                int(args["y2"]),
                duration_s=max(0.0, float(args.get("duration_ms", 400)) / 1000.0),
            )
            if not outcome.ok:
                raise RuntimeError(outcome.detail)
            return "drag"

        if action == "type_text":
            actuator = self._get_actuator()
            text = str(args.get("text", ""))
            actuator.type_text(text)
            return f"typed {text[:40]!r}"

        if action == "hotkey":
            actuator = self._get_actuator()
            keys = [str(k) for k in (args.get("keys") or [])]
            if not keys:
                raise ValueError("hotkey needs at least one key")
            actuator.key_combo(keys)
            return f"key {'+'.join(keys)}"

        if action == "scroll":
            actuator = self._get_actuator()
            direction = str(args.get("direction", "down"))
            amount = max(1, int(args.get("amount", 3)))
            x = args.get("x")
            y = args.get("y")
            actuator.scroll(
                direction,
                amount * _SCROLL_UNIT,
                x=int(x) if x is not None else None,
                y=int(y) if y is not None else None,
            )
            return f"scroll {direction} x{amount}"

        if action == "switch_window":
            from jarvis.platform import window_state  # noqa: PLC0415

            ok, message = window_state.focus_window(str(args.get("title_contains", "")))
            if not ok:
                raise RuntimeError(message)
            return message

        if action == "open_app":
            return self._open_app(str(args.get("app_name", "")))

        raise ValueError(f"unknown screen action {action!r}")

    def _open_app(self, app_name: str) -> str:
        """Launch (or focus) an application inside this screen.

        Deliberately modest compared with the host's app-inventory tool: a
        fresh isolated session has no learned inventory, so the honest
        contract is "run what I was told to run, then wait for its window".
        Focusing an already-open window first keeps a second launch from
        piling up duplicate processes.
        """
        name = app_name.strip()
        if not name:
            raise ValueError("open_app needs an app name")

        from jarvis.platform import window_state  # noqa: PLC0415

        existing = window_state.is_app_running(name)
        if existing is not None:
            ok, message = window_state.raise_window(existing)
            if ok:
                return message

        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        resolved = shutil.which(name)
        launch: list[str]
        if resolved:
            launch = [resolved]
        elif sys.platform == "win32":
            launch = ["cmd", "/c", "start", "", name]
        elif sys.platform == "darwin":
            launch = ["open", "-a", name]
        else:
            launch = ["xdg-open", name]
        creationflags = 0
        if sys.platform == "win32":
            from jarvis.core.process_utils import (  # noqa: PLC0415
                NO_WINDOW_CREATIONFLAGS,
            )

            creationflags = NO_WINDOW_CREATIONFLAGS
        subprocess.Popen(  # noqa: S603 — the name comes from the CU action grammar
            launch,
            creationflags=creationflags,
            close_fds=True,
        )
        # Give the window a bounded chance to appear so the next perception
        # frame sees the app rather than an empty desktop.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if window_state.is_app_running(name) is not None:
                return f"launched {name}"
            time.sleep(0.15)
        return f"launched {name} (no window yet)"

    def quit(self, _params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        self._stop.set()
        return ({"ok": True}, b"")

    # -- dispatch ----------------------------------------------------------

    def handle(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        handler = {
            "health": self.health,
            "geometry": self.geometry,
            "grab": self.grab,
            "foreground": self.foreground,
            "ui_snapshot": self.ui_snapshot,
            "typed_text_landed": self.typed_text_landed,
            "click_landed_in_focus": self.click_landed_in_focus,
            "act": self.act,
            "quit": self.quit,
        }.get(method)
        if handler is None:
            return ({"ok": False, "error": f"unknown method {method!r}"}, b"")
        try:
            return handler(params)
        except Exception as exc:  # noqa: BLE001 — one bad verb must not kill
            # the runner: the host is mid-mission and can retry or fail the
            # step, but it cannot restart a sandbox cheaply.
            logger.warning("[screen-runner] %s failed", method, exc_info=True)
            return ({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, b"")


# ---------------------------------------------------------------------------
# Server loops
# ---------------------------------------------------------------------------


def _serve_http(
    server: ScreenServer,
    token: str,
    host: str,
    port: int,
    port_file: Path | None,
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: PLC0415

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # noqa: D102 — silence stderr spam
            return

        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
            if self.headers.get(HEADER_TOKEN, "") != token:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                request = json.loads(raw.decode("utf-8"))
                method = str(request.get("method", ""))
                params = dict(request.get("params") or {})
            except Exception:  # noqa: BLE001
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            envelope, blob = server.handle(method, params)
            if blob:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(HEADER_WIDTH, str(envelope.get("width", 0)))
                self.send_header(HEADER_HEIGHT, str(envelope.get("height", 0)))
                self.send_header(HEADER_FORMAT, str(envelope.get("format", "RGB")))
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            else:
                body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            if method == "quit":
                threading.Thread(target=httpd.shutdown, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), Handler)
    bound_port = httpd.server_address[1]
    if port_file is not None:
        # Written only once the socket is actually listening, so the host
        # never dials a port that is not up yet.
        write_atomic(Path(port_file), str(bound_port).encode("utf-8"))
    logger.info("[screen-runner] http transport on %s:%s", host, bound_port)
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()


def _serve_mailbox(server: ScreenServer, token: str, mailbox: Path) -> None:
    mailbox.mkdir(parents=True, exist_ok=True)
    logger.info("[screen-runner] mailbox transport in %s", mailbox)
    while not server.stopped.is_set():
        requests = sorted(mailbox.glob(f"*{REQ_SUFFIX}"))
        if not requests:
            time.sleep(MAILBOX_POLL_S)
            continue
        for req_path in requests:
            seq = req_path.name[: -len(REQ_SUFFIX)]
            try:
                request = json.loads(req_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # A half-written or vanished request: the host writes
                # atomically, so this is a torn read of a file being replaced.
                # Skipping lets the next poll see the settled file.
                continue
            if str(request.get("token", "")) != token:
                envelope, blob = {"ok": False, "error": "bad token"}, b""
            else:
                envelope, blob = server.handle(
                    str(request.get("method", "")),
                    dict(request.get("params") or {}),
                )
            try:
                if blob:
                    write_atomic(mailbox / f"{seq}{BIN_SUFFIX}", blob)
                # The JSON is written LAST: its existence is the host's proof
                # that the whole answer (payload included) is on disk.
                write_atomic(
                    mailbox / f"{seq}{RES_SUFFIX}",
                    json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
                )
                req_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "[screen-runner] could not answer %s", seq, exc_info=True,
                )


def serve(
    *,
    transport: str,
    token: str,
    kind: str = "xvfb",
    host: str = "127.0.0.1",
    port: int = 0,
    port_file: Path | None = None,
    mailbox: Path | None = None,
) -> None:
    """Run one runner until it is told to quit."""
    server = ScreenServer(kind=kind)
    if transport == "http":
        _serve_http(server, token, host, port, port_file)
    elif transport == "mailbox":
        if mailbox is None:
            raise ValueError("the mailbox transport needs a directory")
        _serve_mailbox(server, token, Path(mailbox))
    else:
        raise ValueError(f"unknown transport {transport!r}")


def _read_token(token_file: Path) -> str:
    """Read the boot token, then remove the file.

    The token never travels in argv (a process listing is world-readable on
    every supported OS). Deleting it after the read keeps a later process on
    the same screen from picking it up.
    """
    token = Path(token_file).read_text(encoding="utf-8").strip()
    try:
        Path(token_file).unlink(missing_ok=True)
    except OSError:
        logger.debug("[screen-runner] token file cleanup failed", exc_info=True)
    if not token:
        raise ValueError("the screen token file is empty")
    return token


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="jarvis-agent-screen-runner")
    parser.add_argument("--transport", choices=("http", "mailbox"), required=True)
    parser.add_argument("--kind", default="xvfb")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", default="")
    parser.add_argument("--mailbox", default="")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("JARVIS_SCREEN_RUNNER_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    token = _read_token(Path(args.token_file))
    serve(
        transport=args.transport,
        token=token,
        kind=args.kind,
        host=args.host,
        port=args.port,
        port_file=Path(args.port_file) if args.port_file else None,
        mailbox=Path(args.mailbox) if args.mailbox else None,
    )
    return 0


__all__ = ["ScreenServer", "main", "serve"]
