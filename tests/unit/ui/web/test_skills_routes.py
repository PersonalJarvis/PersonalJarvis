"""Tests for the skills REST endpoints touched by the on/off + delete + reorder work.

Covered:
- ``POST /{name}/enable|disable`` now persist the choice to the prefs sidecar so
  it survives a registry reload (the old in-memory flip was wiped on reload).
- ``DELETE /{name}`` removes a user skill and prunes its prefs; builtins are
  refused (they would be re-copied on next boot anyway).
- ``PUT /order`` persists a custom list order; ``GET /api/skills`` reflects it,
  appending any skill not in the order after the ordered ones, by name.

The registry is wired with ``prefs.load_state_overrides`` as its loader so the
enable/disable → reload persistence is exercised end-to-end. ``LOCALAPPDATA`` is
redirected so the prefs file lands in a tmp sandbox.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.skills import prefs
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def _make_skill(root: Path, name: str, *, state: str | None = None) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = (
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: test skill\n"
    )
    if state:
        fm += f"state: {state}\n"
    fm += "---\n\n## Body\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


def _client(skills_root: Path) -> tuple[TestClient, SkillRegistry]:
    from jarvis.ui.web.skills_routes import router

    reg = SkillRegistry(
        skills_root, bus=None, state_prefs_loader=prefs.load_state_overrides
    )
    reg.reload_sync()
    app = FastAPI()
    app.state.skill_registry = reg
    app.include_router(router)
    return TestClient(app), reg


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    # NB: distinct from the LOCALAPPDATA sandbox so deletes never touch prefs dir.
    root = tmp_path / "skills_src"
    root.mkdir()
    return root


# ----------------------------------------------------------------------
# enable / disable persistence
# ----------------------------------------------------------------------


def test_disable_persists_and_survives_reload(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")  # VALIDATED = "on"
    client, reg = _client(skills_root)

    res = client.post("/api/skills/alpha/disable")
    assert res.status_code == 200, res.text

    assert prefs.load_state_overrides() == {"alpha": "disabled"}
    assert reg.get("alpha").state == SkillLifecycleState.DISABLED

    reg.reload_sync()  # the old bug reverted here
    assert reg.get("alpha").state == SkillLifecycleState.DISABLED


def test_enable_after_disable_records_active(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    client, reg = _client(skills_root)

    client.post("/api/skills/alpha/disable")
    res = client.post("/api/skills/alpha/enable")
    assert res.status_code == 200, res.text

    assert prefs.load_state_overrides()["alpha"] == "active"
    assert reg.get("alpha").state == SkillLifecycleState.ACTIVE


# ----------------------------------------------------------------------
# delete
# ----------------------------------------------------------------------


def test_delete_removes_user_skill(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    client, _reg = _client(skills_root)

    res = client.delete("/api/skills/alpha")
    assert res.status_code == 200, res.text
    assert res.json()["removed"] is True
    assert not (skills_root / "alpha").exists()

    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert "alpha" not in names


def test_delete_prunes_prefs(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    client, _reg = _client(skills_root)

    client.post("/api/skills/alpha/disable")
    client.delete("/api/skills/alpha")

    assert "alpha" not in prefs.load_state_overrides()


def test_delete_builtin_is_refused(skills_root: Path) -> None:
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

    builtin_name = sorted(BUILTIN_SKILL_NAMES)[0]
    _make_skill(skills_root, builtin_name)
    client, _reg = _client(skills_root)

    res = client.delete(f"/api/skills/{builtin_name}")
    assert res.status_code == 409, res.text
    assert (skills_root / builtin_name).exists()


# ----------------------------------------------------------------------
# bulk delete
# ----------------------------------------------------------------------


def test_bulk_delete_removes_multiple_user_skills(skills_root: Path) -> None:
    for n in ("alpha", "beta", "gamma"):
        _make_skill(skills_root, n)
    client, _reg = _client(skills_root)

    res = client.post("/api/skills/bulk-delete", json={"names": ["alpha", "gamma"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body["deleted"]) == ["alpha", "gamma"]
    assert body["failed"] == []
    assert not (skills_root / "alpha").exists()
    assert not (skills_root / "gamma").exists()
    assert (skills_root / "beta").exists()

    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert names == ["beta"]


def test_bulk_delete_refuses_builtins_but_deletes_the_rest(skills_root: Path) -> None:
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

    builtin_name = sorted(BUILTIN_SKILL_NAMES)[0]
    _make_skill(skills_root, builtin_name)
    _make_skill(skills_root, "alpha")
    client, _reg = _client(skills_root)

    res = client.post(
        "/api/skills/bulk-delete", json={"names": [builtin_name, "alpha"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] == ["alpha"]
    assert [f["name"] for f in body["failed"]] == [builtin_name]
    # The protected built-in is untouched; the user skill is gone.
    assert (skills_root / builtin_name).exists()
    assert not (skills_root / "alpha").exists()


def test_bulk_delete_reports_missing_skill(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    client, _reg = _client(skills_root)

    res = client.post("/api/skills/bulk-delete", json={"names": ["alpha", "ghost"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] == ["alpha"]
    assert [f["name"] for f in body["failed"]] == ["ghost"]


def test_bulk_delete_dedupes_repeated_names(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    client, _reg = _client(skills_root)

    res = client.post(
        "/api/skills/bulk-delete", json={"names": ["alpha", "alpha"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # A doubled name is deleted once, not reported as a failure on the second pass.
    assert body["deleted"] == ["alpha"]
    assert body["failed"] == []


def test_bulk_delete_prunes_prefs(skills_root: Path) -> None:
    _make_skill(skills_root, "alpha")
    _make_skill(skills_root, "beta")
    client, _reg = _client(skills_root)

    client.post("/api/skills/alpha/disable")
    client.post("/api/skills/bulk-delete", json={"names": ["alpha", "beta"]})

    overrides = prefs.load_state_overrides()
    assert "alpha" not in overrides
    assert "beta" not in overrides


# ----------------------------------------------------------------------
# reorder
# ----------------------------------------------------------------------


def test_reorder_persists_and_list_reflects(skills_root: Path) -> None:
    for n in ("alpha", "beta", "gamma"):
        _make_skill(skills_root, n)
    client, _reg = _client(skills_root)

    res = client.put("/api/skills/order", json={"order": ["gamma", "alpha", "beta"]})
    assert res.status_code == 200, res.text
    assert prefs.load_order() == ["gamma", "alpha", "beta"]

    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert names == ["gamma", "alpha", "beta"]


def test_list_appends_unordered_skills_by_name(skills_root: Path) -> None:
    for n in ("alpha", "beta", "gamma"):
        _make_skill(skills_root, n)
    client, _reg = _client(skills_root)

    client.put("/api/skills/order", json={"order": ["gamma"]})

    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert names[0] == "gamma"
    assert names[1:] == ["alpha", "beta"]  # unordered remainder, sorted by name
