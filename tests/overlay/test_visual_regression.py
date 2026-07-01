"""Phase 9.4 — visual regression with Playwright.

Skips entirely when:
  - Playwright is not installed (``pytest.importorskip``)
  - overlay-ui/dist/edge-glow.html is missing (no npm run build was run)
  - PLAYWRIGHT_BROWSERS_PATH points at nothing (browser not
    installed)

Tests verify:
  - typing/clicking states have a visible edge glow (opacity > 0.3)
  - idle/listening/thinking/speaking/hidden have NO glow
    (opacity ~0)
  - prefers-reduced-motion replaces the animation with static opacity 0.4
  - the state display is hidden without ?debug=1, visible with ?debug=1

Snapshots land under ``tests/overlay/__visual__/``. By default no
snapshots are stored — tests measure computed style, not pixels.
Pixel comparison arrives with Phase 9.6+ once the mascot is added.
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
        pytest.skip(f"edge-glow.html not found under {EDGE_GLOW_HTML} — `npm run build`?")


def _skip_if_no_browser() -> None:
    # Playwright throws executable-not-found when the browser is missing.
    # We can't reliably check upfront — we catch the error
    # at launch() and skip then.
    pass


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without stderr spam."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture(scope="module")
def http_server():
    """Local HTTP server for ``dist/``. Chromium blocks ES modules
    over ``file://`` via CORS — so we serve them."""
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
        pytest.skip(f"Playwright browser not available: {exc!r}")


def _opacity_of(page, selector: str) -> float:
    raw = page.evaluate(
        f"() => getComputedStyle(document.querySelector('{selector}')).opacity"
    )
    return float(raw)


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI without Playwright browser install",
)
def test_idle_has_no_glow(browser_ctx, page_url: str) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        # Initial state (no bridge -> applyState('idle')).
        page.wait_for_function(
            "document.documentElement.dataset.state === 'idle'", timeout=2000
        )
        assert _opacity_of(page, ".edge-glow") < 0.05
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI without Playwright browser install",
)
@pytest.mark.parametrize("state", ["typing", "clicking"])
def test_glow_active_in_action_states(browser_ctx, page_url: str, state: str) -> None:
    page = browser_ctx.new_page()
    try:
        page.goto(page_url)
        page.evaluate(f"document.documentElement.dataset.state = '{state}'")
        # Briefly wait for the transition.
        page.wait_for_timeout(300)
        assert _opacity_of(page, ".edge-glow") > 0.5
    finally:
        page.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("PLAYWRIGHT_HAS_BROWSER") != "1",
    reason="CI without Playwright browser install",
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
    reason="CI without Playwright browser install",
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
    reason="CI without Playwright browser install",
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
    reason="CI without Playwright browser install",
)
def test_reduced_motion_uses_static_glow(browser_ctx, page_url: str) -> None:
    """Plan §19.1: prefers-reduced-motion -> opacity 0.4 instead of animation."""
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
