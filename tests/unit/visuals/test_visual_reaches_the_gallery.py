"""An archived visualisation must actually reach the Visualization section.

``store.py`` matches two conventions it does not own — the run-directory slug
that ``outputs_routes`` parses, and the ``tasks/<id>/artifacts/files/`` subtree
that is the only one it will list or serve. Both are read-side rules, and
getting either subtly wrong writes a picture successfully and makes it
invisible forever: no exception, no log line, just a gallery that never shows
what the user asked for.

Unit tests on either side cannot catch that, because each would be asserting
its own idea of the convention. So this drives the REAL route over a real
archive directory and asserts the whole path end to end: the run is listed, the
file is listed, and it comes back with the no-script CSP that makes it safe to
render inside the app.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.outputs_routes import router as outputs_router
from jarvis.visuals.render import render_visual_html
from jarvis.visuals.spec import parse_spec
from jarvis.visuals.store import store_visual


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def client(archive: Path) -> TestClient:
    app = FastAPI()
    app.include_router(outputs_router)
    app.state.outputs_root = archive
    return TestClient(app)


def _draw(archive: Path) -> dict[str, str]:
    spec = parse_spec(
        {
            "title": "How a turn is answered",
            "kind": "flow",
            "items": [{"label": "Listen"}, {"label": "Decide"}],
        },
        source_utterance="visualisier mir den ablauf",  # i18n-allow: DE test vocabulary
    )
    stored = store_visual(
        render_visual_html(spec),
        title=spec.title,
        utterance=spec.source_utterance,
        outputs_root=archive,
    )
    return {"slug": stored.slug, "path": stored.artifact_path}


def test_the_run_is_listed_with_what_was_asked(client: TestClient, archive: Path) -> None:
    drawn = _draw(archive)

    body = client.get("/api/outputs").json()
    sessions = body["sessions"] if isinstance(body, dict) else body
    slugs = [row["slug"] for row in sessions]
    assert drawn["slug"] in slugs

    row = next(r for r in sessions if r["slug"] == drawn["slug"])
    # The tile's label comes from the slug, so the request has to survive it.
    assert "visualisier" in (row.get("utterance") or "")


def test_the_page_is_listed_as_a_deliverable(client: TestClient, archive: Path) -> None:
    drawn = _draw(archive)

    listing = client.get(f"/api/outputs/{drawn['slug']}/artifacts").json()
    paths = [f["path"] for f in listing["files"]]
    assert drawn["path"] in paths, paths


def test_the_gallery_would_classify_it_as_a_visual(client: TestClient, archive: Path) -> None:
    """``useVisualArtifacts`` selects by extension — ``.html`` is kind "page"."""
    drawn = _draw(archive)
    assert drawn["path"].endswith(".html")


def test_it_renders_inline_under_the_no_script_csp(client: TestClient, archive: Path) -> None:
    drawn = _draw(archive)

    response = client.get(
        f"/api/outputs/{drawn['slug']}/files/{drawn['path']}/download",
        params={"disposition": "inline"},
    )
    assert response.status_code == 200
    assert "How a turn is answered" in response.text
    # The picture is served into the app origin; the CSP is what makes that safe.
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp
