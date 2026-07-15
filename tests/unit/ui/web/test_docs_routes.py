"""REST-route tests for the docs "edit this page" open action.

Fix (MEDIUM): ``POST /api/docs/{slug}/open`` used to branch on
``hasattr(os, "startfile")`` else ``Popen(["xdg-open", ...])`` — macOS has
neither, so the else-branch called a non-existent binary and raised a
``FileNotFoundError`` -> HTTP 500. The route now delegates to
``jarvis.platform.open_path.open_file``, the cross-platform helper already
used by the Outputs view's native file actions.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.docs_routes import _safe_doc_error, router


class _FakeRegistry:
    def __init__(self, docs: dict[str, object]) -> None:
        self._docs = docs

    def get(self, slug: str):
        return self._docs.get(slug)

    async def ensure_loaded(self) -> None:
        return None


def _app(doc_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    doc = SimpleNamespace(path=doc_path)
    app.state.doc_registry = _FakeRegistry({"my-doc": doc})
    return app


def test_open_doc_calls_open_file(tmp_path: Path):
    target = tmp_path / "my-doc.md"
    target.write_text("# hi", encoding="utf-8")
    client = TestClient(_app(target))
    with patch("jarvis.platform.open_path.open_file", return_value=True) as opn:
        r = client.post("/api/docs/my-doc/open")
    assert r.status_code == 200
    assert r.json() == {"path": "my-doc.md", "opened": True}
    opn.assert_called_once_with(target.resolve())


def test_open_doc_failure_is_honest_500_not_launcher_crash(tmp_path: Path):
    """No-launcher-found (the macOS FileNotFoundError shape) must degrade to a
    clean 500, never propagate the underlying OS error."""
    target = tmp_path / "my-doc.md"
    target.write_text("# hi", encoding="utf-8")
    client = TestClient(_app(target))
    with patch("jarvis.platform.open_path.open_file", return_value=False):
        r = client.post("/api/docs/my-doc/open")
    assert r.status_code == 500


def test_open_doc_404_for_unknown_slug(tmp_path: Path):
    client = TestClient(_app(tmp_path / "unused.md"))
    r = client.post("/api/docs/does-not-exist/open")
    assert r.status_code == 404


def test_open_doc_404_when_file_deleted_on_disk(tmp_path: Path):
    target = tmp_path / "gone.md"  # never written
    client = TestClient(_app(target))
    r = client.post("/api/docs/my-doc/open")
    assert r.status_code == 404


def test_doc_error_category_does_not_expose_local_path() -> None:
    error = r"read failed: [Errno 2] C:\Users\private-name\secret-doc.md"

    safe = _safe_doc_error(error)

    assert safe == "read failed"
    assert "private-name" not in safe
