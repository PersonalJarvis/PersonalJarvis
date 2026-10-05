"""The figure import lane keeps only figures that pass the same gate CI runs.

A person's own GLB is welcome on the island exactly when it honours the
character contract (docs/agent-society/character-pipeline.md §8): the route
refuses garbage, refuses a figure that faces the wrong way with the gate's own
reasons, and stores + serves a good one under the data dir.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[4]
_FIGURES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "society" / "figures"
_TOOLS = _REPO / "scripts" / "figures" / "glb_tools.py"


def _shipped() -> Path | None:
    """A self-contained figure suitable for the raw one-file import contract."""
    path = _FIGURES / "spirit-gigi.glb"
    return path if path.exists() else None


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
    source = _shipped()
    body = source.read_bytes()  # type: ignore[union-attr]
    expected = _tools().figure_extras(_tools().read_glb(source).doc)  # type: ignore[arg-type]
    res = client.post("/api/society/figures?name=My Hero", content=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["accepted"] is True, payload
    figure = payload["figure"]
    assert figure["file"].startswith("my-hero-") and figure["file"].endswith(".glb")
    assert figure["archetype"] == expected["archetype"]
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


def test_shared_recipe_validation_is_recipe_only_and_fail_closed(client: TestClient):
    safe = {
        "name": "Scout olive",
        "license": "CC0-1.0",
        "source": "KayKit Character Pack / reviewed recipe",
        "recipe": {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "palette": {"primary": "#315d45"},
            "style": "fantasy",
        },
    }
    accepted = client.post("/api/society/figures/share/validate", json=safe)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["shareable"] is True
    assert accepted.json()["draft"]["recipe"]["base"] == "rogue"

    local_model = {
        **safe,
        "recipe": {**safe["recipe"], "model": "/api/society/figures/custom.glb"},
    }
    assert client.post("/api/society/figures/share/validate", json=local_model).status_code == 422

    hidden_payload = {**safe, "recipe": {**safe["recipe"], "payload": "opaque"}}
    hidden = client.post("/api/society/figures/share/validate", json=hidden_payload)
    assert hidden.status_code == 422

    gigi = {
        **safe,
        "recipe": {**safe["recipe"], "archetype": "spirit", "base": "gigi"},
    }
    assert client.post("/api/society/figures/share/validate", json=gigi).status_code == 422

    unreviewed_license = {**safe, "license": "commercial-use-claimed"}
    unreviewed = client.post("/api/society/figures/share/validate", json=unreviewed_license)
    assert unreviewed.status_code == 422




def test_shared_recipe_validation_requires_shipped_reviewed_assets(client: TestClient):
    safe = {
        "name": "Scout olive",
        "license": "CC0-1.0",
        "source": "KayKit Character Pack / reviewed recipe",
        "recipe": {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "palette": {"primary": "#315d45"},
            "style": "fantasy",
        },
    }

    missing_base = {
        **safe,
        "recipe": {**safe["recipe"], "base": "does-not-exist"},
    }
    response = client.post("/api/society/figures/share/validate", json=missing_base)
    assert response.status_code == 422
    assert "base" in response.json()["detail"]

    missing_part = {
        **safe,
        "recipe": {**safe["recipe"], "parts": {"headgear": "headgear-nope"}},
    }
    response = client.post("/api/society/figures/share/validate", json=missing_part)
    assert response.status_code == 422
    assert "part" in response.json()["detail"]

    wrong_slot = {
        **safe,
        "recipe": {**safe["recipe"], "parts": {"back": "headgear-cap"}},
    }
    response = client.post("/api/society/figures/share", json=wrong_slot)
    assert response.status_code == 422
    assert "part" in response.json()["detail"]

    wrong_family = {
        **safe,
        "recipe": {**safe["recipe"], "parts": {"belt": "belt-sash"}},
    }
    response = client.post("/api/society/figures/share/validate", json=wrong_family)
    assert response.status_code == 422
    assert "family" in response.json()["detail"]


def test_shared_catalog_publish_report_and_delist(client: TestClient, tmp_path: Path):
    safe = {
        "name": "Scout olive",
        "license": "CC0-1.0",
        "source": "KayKit Character Pack / reviewed recipe",
        "recipe": {
            "contract": 1,
            "archetype": "biped",
            "base": "rogue",
            "parts": {},
            "palette": {"primary": "#315d45"},
            "style": "fantasy",
        },
    }

    published = client.post("/api/society/figures/share", json=safe)
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["published"] is True
    figure_id = body["figure"]["id"]
    assert body["figure"]["status"] == "active"
    assert body["figure"]["report_count"] == 0
    assert "reports" not in body["figure"]

    duplicate = client.post("/api/society/figures/share", json=safe)
    assert duplicate.status_code == 200
    assert duplicate.json()["published"] is False

    listed = client.get("/api/society/figures/share")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["figures"][0]["id"] == figure_id
    assert listed.json()["figures"][0]["report_count"] == 0
    assert "reports" not in listed.json()["figures"][0]

    assert client.post(
        f"/api/society/figures/share/{figure_id}/report",
        json={"reason": "   "},
    ).status_code == 422
    reported = client.post(
        f"/api/society/figures/share/{figure_id}/report",
        json={"reason": "Misleading preview"},
    )
    assert reported.status_code == 200
    assert reported.json() == {"reported": figure_id, "reports": 1}
    relisted = client.get("/api/society/figures/share").json()["figures"][0]
    assert relisted["report_count"] == 1
    assert "reports" not in relisted

    delisted = client.delete(f"/api/society/figures/share/{figure_id}")
    assert delisted.status_code == 200
    assert delisted.json() == {"delisted": figure_id}
    assert client.get("/api/society/figures/share").json()["total"] == 0
    assert client.post(
        f"/api/society/figures/share/{figure_id}/report",
        json={"reason": "late report"},
    ).status_code == 404
    assert client.post("/api/society/figures/share", json=safe).status_code == 409

    persisted = json.loads((tmp_path / "shared-figures.json").read_text(encoding="utf-8"))
    row = persisted["figures"][figure_id]
    assert row["status"] == "delisted"
    assert row["reports"][0]["reason"] == "Misleading preview"


def test_shared_catalog_rejects_malformed_ids_without_touching_storage(
    client: TestClient,
):
    for share_id in ("nope", "A" * 16, "f" * 17, "../" + "a" * 16):
        assert client.post(
            f"/api/society/figures/share/{share_id}/report",
            json={"reason": "probe"},
        ).status_code == 404
        assert client.delete(f"/api/society/figures/share/{share_id}").status_code == 404


def test_serving_never_leaves_the_figures_folder(client: TestClient):
    assert client.get("/api/society/figures/..%2F..%2Fjarvis.toml").status_code == 404
    assert client.get("/api/society/figures/nope.glb").status_code == 404


def test_hostile_names_never_reach_a_file_outside(client: TestClient, tmp_path: Path):
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"glTF-outside")
    for name in ("..%5Coutside.glb", "C%3Aoutside.glb", ".hidden.glb", "..glb"):
        assert client.get(f"/api/society/figures/{name}").status_code == 404
        assert client.delete(f"/api/society/figures/{name}").status_code == 404
    assert outside.exists()
