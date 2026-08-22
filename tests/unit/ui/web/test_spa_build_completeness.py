"""A half-written frontend build must never be handed to a window.

``npm run build`` runs with ``emptyOutDir``, so ``dist/`` passes through a
state where ``dist/assets/`` has been deleted while the previous
``index.html`` is still on disk. Measured against the live server with a
200 ms poll during a real rebuild on 2026-08-22: 1.8 s of exactly that
document, then 5.2 s with no index at all, then the new build.

Serving that document is the defect. The window paints the boot splash, its
``<script type="module">`` answers 404, and NOTHING else runs — no React, no
bundle watch, no preload recovery — leaving only the blank-window watchdog's
twenty-second clock. Twenty seconds of a near-black window with a small
spinner is the "it goes black when it reloads by itself" report.

These tests pin both halves: the completeness check itself, and the two places
that serve the SPA (the real app and the fast-boot bootstrap) falling back to
the holding page instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.ui.web.fast_bootstrap import FastBootstrap
from jarvis.ui.web.spa_build import (
    HOLDING_MARKER,
    build_is_complete,
    holding_page_html,
    referenced_assets,
)

_INDEX = (
    "<!doctype html><html><head>"
    '<script type="module" crossorigin src="/assets/index-abc123.js"></script>'
    '<link rel="stylesheet" href="/assets/index-def456.css">'
    "</head><body><div id=\"root\"></div></body></html>"
)


def _dist(tmp_path: Path, *, assets: bool = True, index: bool = True) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    if index:
        (dist / "index.html").write_text(_INDEX, encoding="utf-8")
    if assets:
        (dist / "assets" / "index-abc123.js").write_text("//", encoding="utf-8")
        (dist / "assets" / "index-def456.css").write_text("/**/", encoding="utf-8")
    return dist


class TestReferencedAssets:
    def test_finds_every_hashed_output_once(self) -> None:
        assert referenced_assets(_INDEX) == [
            "/assets/index-abc123.js",
            "/assets/index-def456.css",
        ]

    def test_a_page_with_no_build_outputs_yields_nothing(self) -> None:
        assert referenced_assets("<html><body>plain</body></html>") == []


class TestBuildIsComplete:
    def test_a_whole_build_is_complete(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path)
        assert build_is_complete(dist / "index.html", dist) is True

    def test_an_index_whose_entry_bundle_is_gone_is_not(self, tmp_path: Path) -> None:
        # The measured 1.8 s window, exactly.
        dist = _dist(tmp_path)
        (dist / "assets" / "index-abc123.js").unlink()
        assert build_is_complete(dist / "index.html", dist) is False

    def test_a_missing_index_is_not_complete(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path, index=False)
        assert build_is_complete(dist / "index.html", dist) is False

    def test_it_is_never_cached_because_the_files_move_under_it(
        self, tmp_path: Path
    ) -> None:
        # index.html stays byte-identical while dist/assets is emptied — a
        # result keyed on its mtime would be exactly wrong here.
        dist = _dist(tmp_path)
        assert build_is_complete(dist / "index.html", dist) is True
        (dist / "assets" / "index-abc123.js").unlink()
        assert build_is_complete(dist / "index.html", dist) is False

    def test_a_page_referencing_no_assets_is_left_alone(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
        assert build_is_complete(dist / "index.html", dist) is True


class TestHoldingPage:
    def test_it_carries_the_marker_a_waiting_window_looks_for(self) -> None:
        assert HOLDING_MARKER in holding_page_html()

    def test_it_never_reloads_on_a_blind_timer(self) -> None:
        # A meta refresh reloads into the same half-written build over and over.
        assert "http-equiv" not in holding_page_html()

    def test_it_never_paints_a_trademarked_name(self) -> None:
        # The splash shows the user's own assistant name or nothing at all.
        assert "Jarvis" not in holding_page_html()

    def test_it_resolves_the_theme_without_asking_the_backend(self) -> None:
        # Reading [ui] theme meant a synchronous load_config() on the one event
        # loop that is simultaneously serving every asset of the landing build.
        page = holding_page_html()
        assert "jarvis.theme" in page
        assert ":root.dark" in page


async def _drive(app: Any, scope: dict) -> list[dict]:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)
    return sent


def _get(path: str = "/") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "scheme": "http",
        "client": ("127.0.0.1", 50000),
        "headers": [(b"host", b"127.0.0.1:47821")],
    }


class TestFastBootstrapServesTheHoldingPage:
    @pytest.mark.asyncio
    async def test_a_whole_build_is_served_as_is(self, tmp_path: Path) -> None:
        dist = _dist(tmp_path)
        bs = FastBootstrap(dist_dir=dist)
        sent = await _drive(bs._asgi, _get())
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert sent[0]["status"] == 200
        assert b"index-abc123.js" in body

    @pytest.mark.asyncio
    async def test_a_half_written_build_gets_the_holding_page(
        self, tmp_path: Path
    ) -> None:
        dist = _dist(tmp_path)
        (dist / "assets" / "index-abc123.js").unlink()
        bs = FastBootstrap(dist_dir=dist)
        sent = await _drive(bs._asgi, _get())
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert sent[0]["status"] == 200
        assert HOLDING_MARKER.encode() in body
        # And crucially NOT the document whose script tag would 404.
        assert b"index-abc123.js" not in body


class TestTheRealAppServesTheHoldingPage:
    """The same rule on the route a running window actually hits.

    ``WebServer._spa_index_response`` is a method rather than a free function,
    but it reads only module-level paths — so the check can be driven without
    building the whole FastAPI app, which would need a config, a bus and a
    terminal manager for a decision about two files on disk.
    """

    @staticmethod
    def _response(monkeypatch: pytest.MonkeyPatch, dist: Path) -> Any:
        from jarvis.ui.web import server as server_mod

        monkeypatch.setattr(server_mod, "DIST_DIR", dist)
        monkeypatch.setattr(server_mod, "INDEX_FILE", dist / "index.html")
        # The class stands in for an instance: the method reads module-level
        # paths and one static helper, nothing off `self`.
        return server_mod.WebServer._spa_index_response(server_mod.WebServer)

    def test_a_whole_build_is_served_from_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.responses import FileResponse

        dist = _dist(tmp_path)
        assert isinstance(self._response(monkeypatch, dist), FileResponse)

    def test_a_half_written_build_gets_the_holding_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.responses import HTMLResponse

        dist = _dist(tmp_path)
        (dist / "assets" / "index-abc123.js").unlink()
        response = self._response(monkeypatch, dist)
        assert isinstance(response, HTMLResponse)
        assert HOLDING_MARKER.encode() in response.body
        assert b"index-abc123.js" not in response.body

    def test_a_missing_build_still_gets_the_holding_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.responses import HTMLResponse

        dist = _dist(tmp_path, index=False)
        assert isinstance(self._response(monkeypatch, dist), HTMLResponse)
