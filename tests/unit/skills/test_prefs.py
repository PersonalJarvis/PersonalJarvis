"""Tests for the skill-preferences sidecar store (on/off overrides + list order).

The store is a single JSON file at ``user_data_dir()/data/skill_prefs.json``,
written atomically (mirrors ``socials_routes.py``). It records the user's
recorded on/off choice per skill and the custom list order — it does NOT decide
whether a skill may run; that override (and the AP-15 "never force a DRAFT on"
guard) lives in the registry.

``user_data_dir()`` reads ``LOCALAPPDATA`` at call time, so redirecting the env
sandboxes every test cross-platform.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core.paths import skill_prefs_path
from jarvis.skills import prefs


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def test_empty_on_first_run() -> None:
    loaded = prefs.load_prefs()
    assert loaded.order == []
    assert loaded.state == {}


def test_set_state_roundtrip() -> None:
    prefs.set_state("alpha", True)
    prefs.set_state("beta", False)
    assert prefs.load_state_overrides() == {"alpha": "active", "beta": "disabled"}


def test_set_state_toggle_overwrites() -> None:
    prefs.set_state("alpha", True)
    prefs.set_state("alpha", False)
    assert prefs.load_state_overrides() == {"alpha": "disabled"}


def test_set_order_roundtrip() -> None:
    prefs.set_order(["b", "a", "c"])
    assert prefs.load_order() == ["b", "a", "c"]


def test_remove_prunes_both_order_and_state() -> None:
    prefs.set_state("alpha", False)
    prefs.set_order(["alpha", "beta"])

    prefs.remove_skill("alpha")

    assert "alpha" not in prefs.load_state_overrides()
    assert prefs.load_order() == ["beta"]


def test_atomic_write_produces_versioned_json() -> None:
    prefs.set_order(["a"])
    raw = json.loads(skill_prefs_path().read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["order"] == ["a"]


def test_corrupt_file_degrades_to_empty() -> None:
    path = skill_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    loaded = prefs.load_prefs()

    assert loaded.order == []
    assert loaded.state == {}


def test_unknown_state_values_are_ignored() -> None:
    path = skill_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "order": [], "state": {"x": "bogus", "y": "active"}}
        ),
        encoding="utf-8",
    )

    # Only the legal "active"/"disabled" entries survive the load.
    assert prefs.load_state_overrides() == {"y": "active"}
