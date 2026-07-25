"""The match diagnostics endpoints — the "why didn't my skill fire?" surface.

These two routes exist because their absence is what made the problem
invisible for years: the Skills view could list, enable, edit, reorder and
link-check skills, but there was no way to ask "would this sentence reach my
skill?" without talking to the assistant and guessing from the answer.

The properties under test are therefore about TRUST, not features:

* the panel runs the same code the brain runs, so it cannot lie;
* it executes nothing — no template rendering, no invocation event;
* it reports EVERY guard's verdict, not only the one that vetoed, because when
  the answer is "nothing matched", which check ate it is the whole question.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config import JarvisConfig
from jarvis.skills import match_log, prefs, relevance
from jarvis.skills.guards import GUARD_ORDER
from jarvis.skills.registry import SkillRegistry

# Speech input under test — the fixture vocabulary.
FOCUS_TAGS = "[konzentrationsmodus, fokus]"  # i18n-allow
FIRE_UTTERANCE = "aktiviere den fokus und den konzentrationsmodus"  # i18n-allow
UNRELATED_UTTERANCE = "wie ist das wetter in berlin"  # i18n-allow


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    relevance.clear_index_cache()
    match_log.clear()
    yield
    relevance.clear_index_cache()
    match_log.clear()


def _make_skill(
    root: Path,
    name: str,
    *,
    tags: str | None = None,
    description: str = "test skill",
    execution: str | None = None,
    tier: str | None = None,
) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        'schema_version: "1"',
        f"name: {name}",
        f"description: {description}",
    ]
    if tags:
        lines.append(f"tags: {tags}")
    if execution:
        lines.append(f"execution: {execution}")
    if tier:
        lines += ["risk_policy:", f"  default_tier: {tier}"]
    lines += ["---", "", "## Body", ""]
    (folder / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _client(root: Path, *, shadow: bool = False) -> TestClient:
    from jarvis.ui.web.skills_routes import router

    registry = SkillRegistry(
        root, bus=None, state_prefs_loader=prefs.load_state_overrides
    )
    registry.reload_sync()
    config = JarvisConfig()
    config.skills.relevance_shadow = shadow
    app = FastAPI()
    app.state.skill_registry = registry
    app.state.config = config
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills_src"
    root.mkdir()
    _make_skill(root, "focus", tags=FOCUS_TAGS)
    for index in range(6):
        _make_skill(root, f"filler-{index}", description=f"topic{index}")
    return root


# ---------------------------------------------------------------------------
# match-test
# ---------------------------------------------------------------------------


def test_match_test_explains_a_hit(skills_root: Path) -> None:
    client = _client(skills_root)
    response = client.post("/api/skills/match-test", json={"utterance": FIRE_UTTERANCE})
    assert response.status_code == 200
    body = response.json()

    assert body["winner"] == "focus"
    assert body["band"] == "fire"
    assert body["source"] == "relevance"
    assert body["would_fire"] is True
    assert body["autofire_class"] == "instruction"
    assert body["vetoed_by"] is None
    assert body["candidates"][0]["skill_name"] == "focus"
    assert body["candidates"][0]["score"] > 0


def test_match_test_reports_a_total_miss_without_pretending(
    skills_root: Path,
) -> None:
    client = _client(skills_root)
    body = client.post(
        "/api/skills/match-test", json={"utterance": UNRELATED_UTTERANCE}
    ).json()

    assert body["winner"] is None
    assert body["band"] == "none"
    assert body["would_fire"] is False
    # Even with nothing matched, the ladder is present so the maintainer can see
    # that no guard was the reason — the corpus simply had no signal.
    assert [g["guard"] for g in body["guards_evaluated"]] == list(GUARD_ORDER)
    assert all(g["verdict"] == "skipped" for g in body["guards_evaluated"])


def test_match_test_names_the_guard_that_suppressed_a_match(
    skills_root: Path,
) -> None:
    """The single most useful field in the whole response."""
    _make_skill(skills_root, "plugin-github", tags="[github]")
    client = _client(skills_root)

    body = client.post(
        "/api/skills/match-test", json={"utterance": "was ist GitHub?"}
    ).json()

    assert body["winner"] == "plugin-github"
    assert body["would_fire"] is False
    assert body["vetoed_by"] == "definitional_question"
    vetoed = [g for g in body["guards_evaluated"] if g["verdict"] == "veto"]
    assert [g["guard"] for g in vetoed] == ["definitional_question"]


def test_match_test_shows_a_dispatching_skill_as_ranked_but_barred(
    skills_root: Path,
) -> None:
    """cloud-debug's shape: visible in the ranking, never allowed to fire."""
    _make_skill(
        skills_root,
        "cloud-debug",
        tags="[debugsprint]",
        description="Dispatch a bug hunt to the background worker.",
        execution="mission",
    )
    client = _client(skills_root)

    body = client.post(
        "/api/skills/match-test", json={"utterance": "mach mal einen debugsprint"}
    ).json()

    assert body["winner"] == "cloud-debug"
    assert body["autofire_class"] == "dispatching"
    assert body["would_fire"] is False
    assert body["vetoed_by"] == "class_dispatching"


def test_match_test_reports_shadow_mode_honestly(skills_root: Path) -> None:
    """A maintainer must not read "would_fire: true" while shadow is on."""
    client = _client(skills_root, shadow=True)
    body = client.post(
        "/api/skills/match-test", json={"utterance": FIRE_UTTERANCE}
    ).json()

    assert body["shadow_mode"] is True
    assert body["band"] == "fire"
    assert body["would_fire"] is False, "shadow mode must be reflected, not hidden"


def test_match_test_exposes_the_thresholds_it_used(skills_root: Path) -> None:
    client = _client(skills_root)
    body = client.post(
        "/api/skills/match-test", json={"utterance": FIRE_UTTERANCE}
    ).json()

    assert body["thresholds"]["fire"] > body["thresholds"]["hint"] > 0
    assert body["thresholds"]["min_band"] == "fire"
    assert body["corpus"]["skills_indexed"] >= 7


def test_match_test_executes_nothing(skills_root: Path) -> None:
    """No invocation event, and no Jinja rendering.

    A "dry run" that evaluates templates is not dry — so the response carries no
    rendered instructions at all.
    """
    client = _client(skills_root)
    body = client.post(
        "/api/skills/match-test", json={"utterance": FIRE_UTTERANCE}
    ).json()

    assert "instructions" not in body
    assert "directive" not in body
    entry = match_log.recent(1)[0]
    assert entry.dry_run is True
    assert entry.fired is False


def test_match_test_rejects_an_empty_utterance(skills_root: Path) -> None:
    client = _client(skills_root)
    assert client.post("/api/skills/match-test", json={"utterance": ""}).status_code == 422


def test_match_test_needs_a_registry() -> None:
    from fastapi import FastAPI as _FastAPI

    from jarvis.ui.web.skills_routes import router

    app = _FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert (
        client.post("/api/skills/match-test", json={"utterance": "x"}).status_code == 503
    )


# ---------------------------------------------------------------------------
# match-log
# ---------------------------------------------------------------------------


def test_match_log_starts_empty_and_records_decisions(skills_root: Path) -> None:
    client = _client(skills_root)
    assert client.get("/api/skills/match-log").json()["entries"] == []

    client.post("/api/skills/match-test", json={"utterance": FIRE_UTTERANCE})
    body = client.get("/api/skills/match-log").json()

    assert body["total"] == 1
    assert body["capacity"] == match_log.MAX_ENTRIES
    entry = body["entries"][0]
    assert entry["winner"] == "focus"
    assert entry["dry_run"] is True
    assert "konzentrationsmodus" in entry["utterance_preview"]
    assert len(entry["utterance_hash"]) == 12


def test_match_log_is_newest_first(skills_root: Path) -> None:
    client = _client(skills_root)
    client.post("/api/skills/match-test", json={"utterance": UNRELATED_UTTERANCE})
    client.post("/api/skills/match-test", json={"utterance": FIRE_UTTERANCE})

    entries = client.get("/api/skills/match-log").json()["entries"]
    assert entries[0]["winner"] == "focus"
    assert entries[1]["winner"] is None


def test_match_log_filters_by_skill(skills_root: Path) -> None:
    client = _client(skills_root)
    client.post("/api/skills/match-test", json={"utterance": FIRE_UTTERANCE})
    client.post("/api/skills/match-test", json={"utterance": UNRELATED_UTTERANCE})

    filtered = client.get("/api/skills/match-log?skill=focus").json()["entries"]
    assert len(filtered) == 1
    assert filtered[0]["winner"] == "focus"


def test_match_log_respects_the_limit(skills_root: Path) -> None:
    client = _client(skills_root)
    for _ in range(5):
        client.post("/api/skills/match-test", json={"utterance": FIRE_UTTERANCE})
    assert len(client.get("/api/skills/match-log?limit=2").json()["entries"]) == 2
