"""REST contract of the desktop pets (``jarvis/ui/web/pets_routes.py``, docs/pets.md).

Everything runs against the real pet engine and the real TOML writers, pointed
at a tmp directory: one hand-built "built-in" pet under a tmp built-in root,
user pets under a tmp data directory, and ``JARVIS_CONFIG`` at a tmp
``jarvis.toml``. The DesktopApp and the bus are hand-written recorders.
"""
from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.core import config as config_module
from jarvis.core.config import UIConfig
from jarvis.core.events import PetChanged
from jarvis.ui.pets import loader
from jarvis.ui.pets.manifest import MAX_SHEET_BYTES
from jarvis.ui.pets.states import DEFAULT_PET_ID, PET_STATES
from jarvis.ui.web.pets_routes import router

FRAME = 32


def _sheet_png(rows: int = len(PET_STATES), cols: int = 2) -> bytes:
    """A sheet whose every cell holds an opaque square (so rows are non-empty)."""
    image = Image.new("RGBA", (cols * FRAME, rows * FRAME), (0, 0, 0, 0))
    for row in range(rows):
        for col in range(cols):
            left, top = col * FRAME + 8, row * FRAME + 8
            for x in range(left, left + 16):
                for y in range(top, top + 16):
                    image.putpixel((x, y), (40 + row * 20, 120, 200, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _write_builtin(root: Path, pet_id: str) -> None:
    folder = root / pet_id
    folder.mkdir(parents=True)
    (folder / "sheet.png").write_bytes(_sheet_png(rows=1, cols=2))
    (folder / "pet.json").write_text(
        json.dumps(
            {
                "format": "jarvis-pet/1",
                "id": pet_id,
                "name": pet_id.title(),
                "description": "A test pet.",
                "frame_size": FRAME,
                "sheet": "sheet.png",
                "animations": {"idle": {"row": 0, "frames": 2, "fps": 4, "loop": True}},
            }
        ),
        encoding="utf-8",
    )


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class FakeDesktop:
    """The DesktopApp surface the routes call (see docs/pets.md)."""

    def __init__(self, *, applied_live: bool = True) -> None:
        self.calls: list[tuple] = []
        self.visible = True
        self._applied = applied_live

    def set_pet(self, pet_id: str) -> dict:
        self.calls.append(("set_pet", pet_id))
        return {"ok": True, "applied_live": self._applied}

    def set_pet_look(self, scale: float, bubble: bool) -> dict:
        self.calls.append(("set_pet_look", scale, bubble))
        return {"ok": True, "applied_live": self._applied}

    def set_pet_visible(self, visible: bool) -> dict:
        self.calls.append(("set_pet_visible", visible))
        self.visible = visible
        return {"ok": True, "applied_live": self._applied}

    def pet_visible(self) -> bool:
        return self.visible


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    builtin = tmp_path / "builtin"
    _write_builtin(builtin, DEFAULT_PET_ID)
    _write_builtin(builtin, "miso")
    monkeypatch.setattr(loader, "builtin_root", lambda: builtin)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config_module, "DATA_DIR", data)
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[ui]\norb_style = \"pet\"\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(toml))
    monkeypatch.delenv("JARVIS_INSTANCE", raising=False)

    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(ui=UIConfig(orb_style="pet"))
    app.state.bus = RecordingBus()
    app.state.desktop_app = FakeDesktop()
    return SimpleNamespace(
        client=TestClient(app), app=app, toml=toml, data=data, builtin=builtin
    )


def _toml_ui(env: SimpleNamespace) -> dict:
    return tomllib.loads(env.toml.read_text(encoding="utf-8")).get("ui", {})


def _upload(env: SimpleNamespace, **fields: str) -> Any:
    data = {"name": "Pixel", "description": "My own pet."}
    data.update(fields)
    return env.client.post(
        "/api/pets",
        files={"sheet": ("sheet.png", _sheet_png(), "image/png")},
        data=data,
    )


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_list_returns_state_and_every_pet(env) -> None:
    body = env.client.get("/api/pets").json()
    assert body["active"] == DEFAULT_PET_ID
    assert body["scale"] == 1.0
    assert body["bubble"] is True
    assert body["visible"] is True
    ids = [p["id"] for p in body["pets"]]
    assert ids[0] == DEFAULT_PET_ID  # the default pet leads
    assert "miso" in ids
    gigi = body["pets"][0]
    assert gigi["builtin"] is True
    assert gigi["frame_size"] == FRAME
    assert gigi["sheet_url"] == f"/api/pets/{DEFAULT_PET_ID}/sheet.png"
    # Every state is resolved, so no client repeats the fallback table.
    assert set(gigi["animations"]) == set(PET_STATES)
    assert gigi["animations"]["sleeping"] == gigi["animations"]["idle"]
    assert set(gigi) == {
        "id", "name", "description", "builtin", "frame_size", "animations", "sheet_url"
    }


def test_list_reports_the_default_when_the_configured_pet_is_gone(env) -> None:
    env.app.state.config.ui.pet_id = "u0123456789abcdef"
    assert env.client.get("/api/pets").json()["active"] == DEFAULT_PET_ID
    env.app.state.config.ui.pet_id = "none"
    assert env.client.get("/api/pets").json()["active"] == "none"


def test_sheet_is_served_with_safe_headers(env) -> None:
    r = env.client.get(f"/api/pets/{DEFAULT_PET_ID}/sheet.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-cache"  # built-ins change with updates
    assert r.content == (env.builtin / DEFAULT_PET_ID / "sheet.png").read_bytes()


def test_template_is_the_shipped_sheet_template(env) -> None:
    r = env.client.get("/api/pets/template.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "pet-template.png" in r.headers["content-disposition"]
    shipped = Path(loader.__file__).parent / "template" / "template.png"
    assert r.content == shipped.read_bytes()


@pytest.mark.parametrize(
    "pet_id", ["nope", "..", "..%2F..%2Fjarvis.toml", "none", "u0123456789abcdef", "GIGI.png"]
)
def test_unknown_or_malformed_sheet_ids_are_404(env, pet_id: str) -> None:
    assert env.client.get(f"/api/pets/{pet_id}/sheet.png").status_code == 404


# ---------------------------------------------------------------------------
# PUT /active
# ---------------------------------------------------------------------------


def test_put_active_saves_applies_and_announces(env) -> None:
    r = env.client.put("/api/pets/active", json={"pet_id": "Miso"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] == "miso"
    assert body["persisted"] is True
    assert body["applied_live"] is True
    assert _toml_ui(env)["pet_id"] == "miso"
    assert _toml_ui(env)["orb_style"] == "pet"
    assert env.app.state.config.ui.pet_id == "miso"
    assert env.app.state.desktop_app.calls == [("set_pet", "miso")]
    (event,) = env.app.state.bus.events
    assert isinstance(event, PetChanged)
    assert (event.pet_id, event.scale, event.bubble, event.visible) == ("miso", 1.0, True, True)


def test_put_active_accepts_none(env) -> None:
    r = env.client.put("/api/pets/active", json={"pet_id": "none"})
    assert r.status_code == 200
    assert _toml_ui(env)["pet_id"] == "none"


@pytest.mark.parametrize(
    "pet_id,status", [("does-not-exist", 404), ("../x", 400), ("u0123456789abcdef", 404)]
)
def test_put_active_refuses_what_is_not_a_pet(env, pet_id: str, status: int) -> None:
    r = env.client.put("/api/pets/active", json={"pet_id": pet_id})
    assert r.status_code == status
    assert "pet_id" not in _toml_ui(env)
    assert env.app.state.bus.events == []


def test_put_active_headless_saves_and_waits_for_the_next_start(env) -> None:
    env.app.state.desktop_app = None
    r = env.client.put("/api/pets/active", json={"pet_id": "miso"})
    assert r.status_code == 200
    assert r.json()["applied_live"] is False
    assert _toml_ui(env)["pet_id"] == "miso"


def test_a_dev_instance_does_not_change_the_shared_pet(env, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_INSTANCE", "dev")
    assert env.client.put("/api/pets/active", json={"pet_id": "miso"}).status_code == 409
    assert env.client.put("/api/pets/settings", json={"scale": 1.5}).status_code == 409
    assert "pet_id" not in _toml_ui(env)
    assert "pet_scale" not in _toml_ui(env)


# ---------------------------------------------------------------------------
# PUT /settings, POST /visibility
# ---------------------------------------------------------------------------


def test_put_settings_clamps_saves_and_applies(env) -> None:
    r = env.client.put("/api/pets/settings", json={"scale": 5, "bubble": False})
    assert r.status_code == 200
    body = r.json()
    assert (body["scale"], body["bubble"]) == (2.0, False)
    assert _toml_ui(env)["pet_scale"] == 2.0
    assert _toml_ui(env)["pet_bubble"] is False
    assert env.app.state.desktop_app.calls == [("set_pet_look", 2.0, False)]
    (event,) = env.app.state.bus.events
    assert (event.scale, event.bubble) == (2.0, False)


def test_put_settings_changes_only_what_is_sent(env) -> None:
    env.client.put("/api/pets/settings", json={"bubble": False})
    assert "pet_scale" not in _toml_ui(env)
    assert env.app.state.desktop_app.calls == [("set_pet_look", 1.0, False)]


def test_put_settings_needs_something_to_change(env) -> None:
    assert env.client.put("/api/pets/settings", json={}).status_code == 400


def test_visibility_is_runtime_only(env) -> None:
    before = env.toml.read_text(encoding="utf-8")
    r = env.client.post("/api/pets/visibility", json={"visible": False})
    assert r.status_code == 200
    assert r.json()["visible"] is False
    assert env.app.state.desktop_app.calls == [("set_pet_visible", False)]
    assert env.toml.read_text(encoding="utf-8") == before
    assert env.app.state.bus.events[-1].visible is False
    assert env.client.get("/api/pets").json()["visible"] is False


# ---------------------------------------------------------------------------
# POST (create) and DELETE
# ---------------------------------------------------------------------------


def test_upload_creates_a_pet_that_lists_and_serves(env) -> None:
    r = _upload(env)
    assert r.status_code == 201, r.text
    pet = r.json()
    assert pet["id"].startswith("u") and len(pet["id"]) == 17
    assert pet["name"] == "Pixel"
    assert pet["builtin"] is False
    assert pet["sheet_url"] == f"/api/pets/{pet['id']}/sheet.png"

    ids = [p["id"] for p in env.client.get("/api/pets").json()["pets"]]
    assert pet["id"] in ids
    sheet = env.client.get(pet["sheet_url"])
    assert sheet.status_code == 200
    assert "immutable" in sheet.headers["cache-control"]


def test_upload_with_a_manifest_and_frame_size(env) -> None:
    manifest = json.dumps(
        {"animations": {"idle": {"row": 0, "frames": 2, "fps": 6, "loop": True}}}
    )
    r = _upload(env, manifest=manifest, frame_size="32")
    assert r.status_code == 201, r.text
    assert r.json()["animations"]["idle"]["fps"] == 6


@pytest.mark.parametrize(
    "sheet",
    [b"", b"GIF89a not a png", b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_SHEET_BYTES + 10)],
    # Explicit ids: the default id of a 2 MB bytes value is the value itself,
    # and pytest plugins put the test id into an environment variable, which
    # Windows caps at 32 767 characters.
    ids=["empty", "not-a-png", "oversize"],
)
def test_upload_refusals_are_400_with_a_sentence(env, sheet: bytes) -> None:
    r = env.client.post(
        "/api/pets",
        files={"sheet": ("sheet.png", sheet, "image/png")},
        data={"name": "Pixel", "description": ""},
    )
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert not any(env.data.glob("pets/u*"))


def test_upload_rejects_a_frame_size_that_is_not_a_number(env) -> None:
    assert _upload(env, frame_size="big").status_code == 400


def test_upload_rejects_a_manifest_that_is_not_json(env) -> None:
    r = _upload(env, manifest="{not json")
    assert r.status_code == 400
    assert "JSON" in r.json()["detail"]


def test_delete_removes_a_user_pet(env) -> None:
    pet_id = _upload(env).json()["id"]
    r = env.client.delete(f"/api/pets/{pet_id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": pet_id, "active": DEFAULT_PET_ID}
    assert env.client.get(f"/api/pets/{pet_id}/sheet.png").status_code == 404
    assert env.app.state.bus.events == []  # the active pet did not change


def test_deleting_the_active_pet_falls_back_to_the_default(env) -> None:
    pet_id = _upload(env).json()["id"]
    env.client.put("/api/pets/active", json={"pet_id": pet_id})
    env.app.state.desktop_app.calls.clear()
    env.app.state.bus.events.clear()

    r = env.client.delete(f"/api/pets/{pet_id}")
    assert r.status_code == 200
    assert r.json()["active"] == DEFAULT_PET_ID
    assert _toml_ui(env)["pet_id"] == DEFAULT_PET_ID
    assert env.app.state.desktop_app.calls == [("set_pet", DEFAULT_PET_ID)]
    (event,) = env.app.state.bus.events
    assert (event.pet_id, event.source) == (DEFAULT_PET_ID, "pet_deleted")


def test_built_in_pets_cannot_be_deleted(env) -> None:
    r = env.client.delete(f"/api/pets/{DEFAULT_PET_ID}")
    assert r.status_code == 400
    assert (env.builtin / DEFAULT_PET_ID / "pet.json").is_file()


@pytest.mark.parametrize("pet_id", ["u0123456789abcdef", "..", "none"])
def test_deleting_something_that_is_not_a_user_pet_changes_nothing(env, pet_id: str) -> None:
    assert env.client.delete(f"/api/pets/{pet_id}").status_code in (400, 404)


def test_delete_is_flagged_dangerous_for_the_cli(env) -> None:
    spec = env.app.openapi()
    operation = spec["paths"]["/api/pets/{pet_id}"]["delete"]
    assert operation.get("x-jarvis-dangerous") is True
    assert operation["tags"] == ["pets"]
