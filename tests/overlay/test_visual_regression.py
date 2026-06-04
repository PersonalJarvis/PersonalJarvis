"""Phase 9.4 — Visual-Regression mit Playwright.

Skipt komplett wenn:
  - Playwright nicht installiert (``pytest.importorskip``)
  - overlay-ui/dist/edge-glow.html fehlt (kein npm run build gelaufen)
  - PLAYWRIGHT_BROWSERS_PATH zeigt auf nichts (Browser nicht
    installiert)

Tests pruefen:
  - typing/clicking States haben sichtbaren Edge-Glow (opacity > 0.3)
  - idle/listening/thinking/speaking/hidden haben KEINEN Glow
    (opacity ~0)
  - prefers-reduced-motion ersetzt Animation durch static opacity 0.4
  - State-Display ist hidden ohne ?debug=1, sichtbar mit ?debug=1

Snapshots landen unter ``tests/overlay/__visual__/``. Per Default werden
keine Snapshots gespeichert — Tests messen Computed-Style, nicht Pixel.
Pixel-Vergleich kommt mit Phase 9.6+ wenn das Mascot dazu kommt.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "OS-Level" / "overlay-ui" / "dist"
EDGE_GLOW_HTML = DIST_DIR / "edge-glow.html"


def _skip_if_no_build() -> None:
    if not EDGE_GLOW_HTML.is_file():
        pytest.skip(f"edge-glow.html nicht gefunden unter {EDGE_GLOW_HTML} — `npm run build`?")


def _skip_if_no_browser() -> None:
    # Playwright wirft Executable-not-found wenn der Browser fehlt.
    # Wir koennen vorab nicht zuverlaessig pruefen — fangen den Fehler
    # beim launch() ab und skippen dann.
    pass


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler ohne stderr-Spam."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture(scope="module")
def http_server():
    """Lokaler HTTP-Server fuer ``dist/``. Chromium blockt ES-Modules
    ueber ``file://`` per CORS — daher serven."""
    _skip_if_no_build()

    cwd = str(DIST_DIR)

    class _RootedHandler(_SilentHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=cwd, **kwargs)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _RootedHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.fixture(scope="module")
def page_url(http_server: str) -> str:
    return f"{http_server}/edge-glow.html"


@pytest.fixture(scope="module")
def browser_ctx():
    _skip_if_no_browser()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright-Browser nicht verfuegbar: {exc!r}")


def _opacity_of(page, selector: str) -> float:
    raw = page.evaluate(
        f"() => getComputedStyle(document.querySelector('{selector}')).opacity"
    )
    return float(raw)


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
def test_idle_has_no_glow(browser_ctx, page_url: str) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        # Init-State (kein Bridge -> applyState('idle')).
        page.wait_for_function(
            "document.documentElement.dataset.state === 'idle'", timeout=2000
        )
        assert _opacity_of(page, ".edge-glow") < 0.05
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
@pytest.mark.parametrize("state", ["typing", "clicking"])
def test_glow_active_in_action_states(browser_ctx, page_url: str, state: str) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        page.evaluate(f"document.documentElement.dataset.state = '{state}'")
        # Kurz auf transition warten.
        page.wait_for_timeout(300)
        assert _opacity_of(page, ".edge-glow") > 0.5
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
@pytest.mark.parametrize(
    "state", ["listening", "thinking", "speaking", "hidden"]
)
def test_no_glow_in_non_action_states(
    browser_ctx, page_url: str, state: str
) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        page.evaluate(f"document.documentElement.dataset.state = '{state}'")
        page.wait_for_timeout(300)
        assert _opacity_of(page, ".edge-glow") < 0.05
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
def test_state_display_hidden_without_debug_flag(
    browser_ctx, page_url: str
) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        page.wait_for_function(
            "document.documentElement.dataset.state === 'idle'", timeout=2000
        )
        display = page.evaluate(
            "() => getComputedStyle(document.querySelector('#state-display')).display"
        )
        assert display == "none"
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
def test_state_display_visible_with_debug_flag(
    browser_ctx, page_url: str
) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(f"{page_url}?debug=1")
        page.wait_for_function(
            "document.documentElement.dataset.state === 'idle'", timeout=2000
        )
        display = page.evaluate(
            "() => getComputedStyle(document.querySelector('#state-display')).display"
        )
        assert display != "none"
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI ohne Playwright-Browser-Install",
)
def test_reduced_motion_uses_static_glow(browser_ctx, page_url: str) -> None:
    """Plan §19.1: prefers-reduced-motion -> opacity 0.4 statt animation."""
    page = browser_ctx.new_page()
    page.emulate_media(reduced_motion="reduce")
    try:
        page.goto(page_url)
        page.evaluate("document.documentElement.dataset.state = 'typing'")
        page.wait_for_timeout(300)
        opacity = _opacity_of(page, ".edge-glow")
        # Static low-intensity per Plan = 0.4 (+/- transition).
        assert 0.3 <= opacity <= 0.5, f"reduced-motion glow opacity={opacity}"
    finally:
        page.close()
