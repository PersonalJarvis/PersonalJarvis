"""A desktop window attached to a server it neither owns nor stops.

The same origin serves the UI, API, streams and login. No provider credentials,
databases, scheduler or agent process is created by this client. Native window
chrome belongs to the client; server-side desktop routes retain their browser
fallbacks instead of controlling a different machine's window.
"""

from __future__ import annotations

import hashlib
import logging
import random
import sys
import time
from collections.abc import Callable

from jarvis.core.server_endpoint import server_endpoint

log = logging.getLogger(__name__)


def wait_for_server(url: str, *, timeout_s: float = 120.0) -> None:
    """Await the real backend through one reused HTTP client; never infer readiness."""
    from urllib.parse import urlsplit

    import httpx

    # Only local startup uses the machine's control credential. Remote clients
    # authenticate in the server's own page, never by forwarding this key.
    server_endpoint(url)
    if urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Readiness checks with local credentials require a loopback server.")
    from jarvis.core.control_key import get_control_key
    from jarvis.core.http_pool import SyncHttpClientPool

    deadline = time.monotonic() + timeout_s
    # An explicit transport disables environment proxy discovery in httpx:
    # the loopback control credential must never travel through a proxy.
    pool = SyncHttpClientPool(timeout_s=2.0, transport=httpx.HTTPTransport())
    with pool.client() as client:
        while time.monotonic() < deadline:
            try:
                key = get_control_key()
                response = client.get(
                    url + "/api/agent-server/status",
                    headers={"Authorization": "Bearer " + key} if key else {},
                )
                if response.status_code == 200:
                    status = response.json()
                    if status.get("error"):
                        raise RuntimeError(status["error"])
                    if status.get("independent") and status.get("ready"):
                        return
            except (httpx.HTTPError, ValueError):
                log.debug("Waiting for the agent server to become ready")
            time.sleep(random.uniform(0.2, 0.35))  # noqa: S311 - readiness jitter, not a secret
    raise RuntimeError("The agent server did not become ready. Its log contains the startup error.")


def local_server(*, port: int, spawn: Callable[..., bool] | None = None) -> str:
    """Start or reuse the independently owned server of this instance."""
    from filelock import FileLock

    from jarvis.core import background_service as bg

    # Concurrent clients must not start competing owners of the same databases.
    lock_path = bg.marker_path().with_suffix(".launch.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=130):
        marker = bg.read_marker() or {}
        pid = bg.service_pid()
        if pid is not None:
            if not marker.get("persistent"):
                raise RuntimeError(
                    "A legacy background session is still running. Stop it before enabling "
                    "the persistent server; opening a client will not interrupt its agents."
                )
            port = int(marker.get("port") or port)
        else:
            from jarvis.ui.desktop_app import _pid_alive, _read_meta

            existing = _read_meta() or {}
            existing_pid = int(existing.get("pid") or 0)
            if existing_pid > 0 and _pid_alive(existing_pid):
                raise RuntimeError(
                    "The integrated desktop is still running. Close it before starting "
                    "the independent server. No existing work has been stopped."
                )
            start = spawn or bg.spawn_service
            if not start(after_pid=None, persistent=True, port=port):
                raise RuntimeError("The independent agent server could not be started.")
        url = server_endpoint(f"http://127.0.0.1:{port}")
        wait_for_server(url)
        marker = bg.read_marker() or {}
        if not marker.get("persistent") or bg.service_pid() is None:
            raise RuntimeError("The answering backend is not the persistent agent server.")
        return url


def run(url: str | None, *, port: int = 47821) -> int:
    """Show the existing web application; closing this process only disconnects it."""
    import webview

    from jarvis.core.branding import PRODUCT_NAME
    from jarvis.core.instance import current_instance
    from jarvis.core.paths import user_data_dir

    origin = server_endpoint(url or f"http://127.0.0.1:{port}")
    # Origins and app instances have separate cookies, layouts and local data.
    digest = hashlib.sha256(origin.encode()).hexdigest()[:20]
    storage = user_data_dir() / "server-clients" / current_instance().name / digest
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        PRODUCT_NAME, origin if url else None,
        html=None if url else _holding_page("Starting the agent server…"),
        width=1280, height=850, min_size=(800, 600),
    )

    def connect_local() -> None:
        try:
            endpoint = local_server(port=port)
            if not window.events.closed.is_set():
                window.load_url(endpoint)
        except Exception:
            log.exception("The desktop client could not connect to its agent server")
            if not window.events.closed.is_set():
                window.load_html(_holding_page(
                    "The agent server could not start. Check the server log and reopen the client."
                ))

    webview.start(
        func=connect_local if url is None else None,
        gui="edgechromium" if sys.platform == "win32" else None,
        private_mode=False,
        storage_path=str(storage),
    )
    return 0


def _holding_page(message: str) -> str:
    import html

    return (
        '<!doctype html><meta charset="utf-8"><meta name="color-scheme" content="light dark">'
        '<style>body{font:16px system-ui;display:grid;place-items:center;min-height:90vh;'
        'margin:0;padding:24px}p{max-width:42rem;text-align:center}</style>'
        f"<p>{html.escape(message)}</p>"
    )
