"""Avatar JSON companion parity, SQLite persistence and headless API round trips."""

import json
import re
from pathlib import Path
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.society.companion import AccessorySlot, CompanionAppearance, validate_avatar_companion
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router

CASES = json.loads((Path(__file__).parent / "fixtures/agent-companion.json").read_text())
CATALOG = Path(__file__).resolve().parents[2] / (
    "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"
)


@pytest.mark.parametrize("case", CASES["valid"])
def test_shared_companion_contract_accepts(case):
    assert CompanionAppearance.model_validate(case).shape == case["shape"]


@pytest.mark.parametrize("case", CASES["invalid"])
def test_shared_companion_contract_rejects(case):
    with pytest.raises(ValidationError):
        CompanionAppearance.model_validate(case)


def test_accessory_slots_and_ids_match_the_frontend_catalog():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert list(get_args(AccessorySlot)) == catalog["slots"]
    ids = [item["id"] for item in catalog["items"]]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9_]{1,40}", item_id) for item_id in ids)
    # Slots may stay empty (the set is small and finished), but never name an unknown slot.
    assert {item["slot"] for item in catalog["items"]} <= set(catalog["slots"])


def test_worn_accessories_survive_and_bare_looks_stay_unchanged():
    worn = validate_avatar_companion({"companion": CASES["valid"][3]})["companion"]
    assert worn["accessories"] == CASES["valid"][3]["accessories"]
    bare = validate_avatar_companion({"companion": CASES["valid"][0]})["companion"]
    assert "accessories" not in bare


def test_legacy_and_imported_characters_survive_companion_validation():
    old = {
        "contract": 1,
        "base": "rogue",
        "model": "/api/society/figures/custom.glb",
        "parts": {"head": "hat"},
    }
    assert validate_avatar_companion(old) == old
    updated = validate_avatar_companion({**old, "companion": CASES["valid"][0]})
    assert {k: v for k, v in updated.items() if k != "companion"} == old


def test_fresh_headless_create_edit_reload_without_inference_or_credentials(tmp_path):
    """This visual feature works with no provider key, GPU, microphone or window."""
    avatar = {
        "contract": 1,
        "archetype": "biped",
        "base": "rogue",
        "parts": {},
        "companion": CASES["valid"][0],
    }

    def open_app():
        runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
        app = FastAPI()
        app.include_router(router)
        app.state.society_factory = lambda: runtime
        return app, runtime

    app, runtime = open_app()
    with TestClient(app) as client:
        try:
            response = client.post(
                "/api/society/agents", json={"name": "Companion contract", "avatar": avatar}
            )
            assert response.status_code == 200, response.text
            agent = response.json()["agent"]
            url = f"/api/society/agents/{agent['agent_id']}"
            updated = {**avatar, "companion": CASES["valid"][1]}
            response = client.patch(url, json={"avatar": updated})
            assert response.status_code == 200, response.text
            assert response.json()["agent"]["avatar"]["companion"]["enabled"] is False
            bad = client.patch(url, json={"avatar": {**avatar, "companion": CASES["invalid"][0]}})
            assert bad.status_code == 409
        finally:
            client.portal.call(runtime.close)
    app, runtime = open_app()
    with TestClient(app) as client:
        try:
            restored = client.get(url).json()["agent"]["avatar"]
            assert restored == updated
        finally:
            client.portal.call(runtime.close)
