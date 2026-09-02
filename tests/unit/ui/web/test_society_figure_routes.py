"""The figure import lane keeps only figures that pass the same gate CI runs.

A person's own GLB is welcome on the island exactly when it honours the
character contract (docs/agent-society/character-pipeline.md §8): the route
refuses garbage, refuses a figure that faces the wrong way with the gate's own
reasons, and stores + serves a good one under the data dir.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[4]
_FIGURES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "society" / "figures"
_TOOLS = _REPO / "scripts" / "figures" / "glb_tools.py"


def _shipped() -> Path | None:
    files = sorted(_FIGURES.glob("biped-*.glb")) if _FIGURES.exists() else []
    return files[0] if files else None


def _tools():
    if "glb_tools" in sys.modules:
        return sys.modules["glb_tools"]
    spec = importlib.util.spec_from_file_location("glb_tools", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["glb_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from jarvis.ui.web import society_figure_routes as routes

    monkeypatch.setattr(routes, "figures_dir", lambda: tmp_path / "figures")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_garbage_is_refused_as_not_a_glb(client: TestClient):
    res = client.post("/api/society/figures?name=x", content=b"hello world, not a model")
    assert res.status_code == 400


@pytest.mark.skipif(_shipped() is None, reason="no figure asset built yet")
def test_a_contract_figure_is_accepted_stored_and_served(client: TestClient):
    body = _shipped().read_bytes()  # type: ignore[union-attr]
    res = client.post("/api/society/figures?name=My Hero", content=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["accepted"] is True, payload
    figure = payload["figure"]
    assert figure["file"].startswith("my-hero-") and figure["file"].endswith(".glb")
    assert figure["archetype"] == "biped"
    assert "walk" in figure["clips"]

    listed = client.get("/api/society/figures").json()
    assert [f["file"] for f in listed["figures"]] == [figure["file"]]

    served = client.get(figure["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("model/gltf-binary")
    assert served.content == body

    assert client.delete(figure["url"]).status_code == 200
    assert client.get("/api/society/figures").json()["total"] == 0


@pytest.mark.skipif(_shipped() is None, reason="no figure asset built yet")
def test_a_figure_facing_the_wrong_way_is_refused_with_the_reason(
    client: TestClient, tmp_path: Path
):
    gt = _tools()
    glb = gt.read_glb(_shipped())
    names = gt.node_index_by_name(glb.doc)
    glb.doc["nodes"][names["FWD"]]["translation"] = [0.0, 0.5, -1.0]
    broken = tmp_path / "turned.glb"
    gt.write_glb(broken, glb.doc, glb.blob)

    res = client.post("/api/society/figures?name=turned", content=broken.read_bytes())
    assert res.status_code == 200
    payload = res.json()
    assert payload["accepted"] is False
    assert any("faces the wrong way" in p for p in payload["problems"])
    assert client.get("/api/society/figures").json()["total"] == 0


def test_serving_never_leaves_the_figures_folder(client: TestClient):
    assert client.get("/api/society/figures/..%2F..%2Fjarvis.toml").status_code == 404
    assert client.get("/api/society/figures/nope.glb").status_code == 404
