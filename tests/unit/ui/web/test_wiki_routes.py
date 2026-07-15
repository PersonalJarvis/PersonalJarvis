"""Unit tests for the Phase B3 Wiki-view REST routes.

Mounts ``wiki_routes.router`` on a fresh FastAPI app, points the
``app.state.config`` at a temporary vault directory, and asserts the
JSON shapes defined in ``docs/plans/b3/00-OVERVIEW.md §3.1``.

The tests use real files in ``tmp_path`` (AP-5 forbids mocking the
filesystem). They never write to the real ``wiki/obsidian-vault/``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import wiki_routes
from jarvis.ui.web.wiki_routes import router as wiki_router

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_wiki_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep process-wide Wiki health mutations local to each route test."""
    from jarvis.memory.wiki.health import WikiHealth

    monkeypatch.setattr("jarvis.memory.wiki.health.health", WikiHealth())


def _make_app(vault_root: Path | None) -> FastAPI:
    """Build a minimal FastAPI app with the wiki router mounted.

    ``vault_root=None`` simulates a wiki-integration-disabled config.
    """
    app = FastAPI()
    app.include_router(wiki_router)
    if vault_root is None:
        wiki_cfg = SimpleNamespace(vault_root=None)
    else:
        wiki_cfg = SimpleNamespace(vault_root=vault_root)
    data_dir = vault_root.parent / "data" if vault_root is not None else Path("data")
    app.state.config = SimpleNamespace(
        wiki_integration=wiki_cfg,
        memory=SimpleNamespace(data_dir=data_dir),
    )
    return app


def _write_page(
    vault_root: Path,
    subdir: str,
    slug: str,
    *,
    page_type: str,
    body: str,
    extra_fm: dict[str, str] | None = None,
) -> Path:
    """Helper: write a schema-valid markdown page into the vault."""
    folder = vault_root / subdir
    folder.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"type: {page_type}",
        f"slug: {slug}",
    ]
    if page_type == "project":
        fm_lines.append("status: active")
    for key, value in (extra_fm or {}).items():
        fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    path = folder / f"{slug}.md"
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


@pytest.fixture
def populated_vault(tmp_path: Path) -> Path:
    """Three-page vault: ruben -> harald, ruben -> pixel-art-editor."""
    vault = tmp_path / "vault"
    _write_page(
        vault,
        "entities",
        "harald",
        page_type="entity",
        body=(
            "# Harald\n\n## Summary\nHarald is a person born in 1976.\n\n"
            "## Facts\n- Born in 1976.\n"
        ),
    )
    _write_page(
        vault,
        "entities",
        "ruben",
        page_type="entity",
        body=(
            "# Ruben\n\n## Summary\nFather is [[harald]].\n\n"
            "## Facts\n- Working on [[pixel-art-editor]].\n"
            "- Favorite food is Pizza (source: voice-fact:demo).\n"
        ),
    )
    _write_page(
        vault,
        "projects",
        "pixel-art-editor",
        page_type="project",
        body="# Pixel Art Editor\n\n## Goal\nTiny pixel-art editor in Rust.\n",
    )
    return vault


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """Vault directory with the four standard subfolders, but no pages."""
    vault = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    return vault


class _FakeCurator:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def ingest(self, text: str, source: str) -> object:
        self.calls.append((text, source))
        return self.result


class _FakeBackfillResult:
    review_keys: tuple[str, ...] = ()
    attempted_review_keys: tuple[str, ...] = ()
    sessions_failed = 0
    sessions_in_progress = 0

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "dry_run": True,
            "days": 2,
            "sessions_scanned": 3,
            "sessions_eligible": 2,
            "sessions_already_reviewed": 1,
            "sessions_in_progress": 0,
            "sessions_reviewed": 0,
            "sessions_failed": 0,
            "turns_considered": 7,
            "candidates_journaled": 0,
        }


# ----------------------------------------------------------------------
# /tree
# ----------------------------------------------------------------------


def test_tree_with_three_pages_lists_files_and_counts(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    folders_by_name = {f["name"]: f for f in body["folders"]}
    assert folders_by_name["entities"]["count"] == 2
    assert folders_by_name["projects"]["count"] == 1
    assert folders_by_name["concepts"]["count"] == 0
    assert folders_by_name["sessions"]["count"] == 0
    slugs = {f["slug"] for f in folders_by_name["entities"]["files"]}
    assert slugs == {"harald", "ruben"}
    sample_file = folders_by_name["entities"]["files"][0]
    assert "mtime" in sample_file and isinstance(sample_file["mtime"], float)
    assert "size" in sample_file and sample_file["size"] > 0
    assert body["stats"]["total_pages"] == 3
    # ruben has 2 outbound wikilinks (harald, pixel-art-editor)
    assert body["stats"]["total_links"] >= 2


def test_tree_with_empty_vault_returns_four_empty_buckets(empty_vault: Path) -> None:
    app = _make_app(empty_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert len(body["folders"]) == 4
    for folder in body["folders"]:
        assert folder["count"] == 0
        assert folder["files"] == []
    assert body["stats"]["total_pages"] == 0


def test_tree_with_missing_vault_returns_empty_ok_response(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    app = _make_app(missing)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert body["stats"]["total_pages"] == 0
    assert all(folder["files"] == [] for folder in body["folders"])


# ----------------------------------------------------------------------
# /page/{slug}
# ----------------------------------------------------------------------


def test_page_happy_path_returns_frontmatter_body_wikilinks(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/ruben")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "ruben"
    assert body["kind"] == "entity"
    assert body["frontmatter_valid"] is True
    assert body["frontmatter"]["type"] == "entity"
    assert "Father is [[harald]]" in body["body_md"]
    assert set(body["wikilinks"]) == {"harald", "pixel-art-editor"}
    assert body["stats"]["bytes"] > 0
    assert body["stats"]["words"] > 0
    assert body["path"].endswith("entities/ruben.md")


def test_page_unknown_slug_returns_not_found_envelope(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/does-not-exist")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False
    assert "not found" in body["error"]


def test_page_rejects_path_traversal_slug(populated_vault: Path) -> None:
    """A slug that could escape the vault must be rejected before any disk
    probe. On Windows a backslash is a valid single URL path segment, so
    ``..\\..\\x`` reaches the handler and ``vault_root / dir / f"{slug}.md"``
    would resolve outside the vault. The guard must reject it.
    """
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        for bad in ("..\\..\\secret", "..\\..\\..\\Windows\\win", "foo\\bar", "a:b"):
            r = client.get(f"/api/wiki/page/{bad}")
            body = r.json()
            assert body["ok"] is False, f"{bad!r} should be rejected"
            assert "invalid" in body["error"], f"{bad!r} gave {body}"


def test_page_schema_invalid_still_returns_page_with_flag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    folder = vault / "entities"
    folder.mkdir(parents=True)
    # Missing 'slug' frontmatter key — schema validation fails, but the
    # endpoint must still return the page so the UI can warn.
    (folder / "broken.md").write_text(
        "---\ntype: entity\n---\n\n# Broken\n\nBody text.\n",
        encoding="utf-8",
    )
    app = _make_app(vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/broken")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "broken"
    assert body["frontmatter_valid"] is False
    assert "Body text" in body["body_md"]


# ----------------------------------------------------------------------
# /graph
# ----------------------------------------------------------------------


def test_graph_with_linked_pages_produces_nodes_and_edges(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/graph")
    body = r.json()
    assert body["ok"] is True
    node_ids = {n["id"] for n in body["nodes"]}
    assert node_ids == {"harald", "ruben", "pixel-art-editor"}
    edge_pairs = {(e["source"], e["target"]) for e in body["edges"]}
    assert ("ruben", "harald") in edge_pairs
    assert ("ruben", "pixel-art-editor") in edge_pairs
    assert body["broken"] == []
    # Edge contexts include the wikilink in question.
    for edge in body["edges"]:
        assert edge["context"] != ""


def test_graph_with_broken_wikilink_lists_it_in_broken_bucket(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_page(
        vault,
        "entities",
        "alice",
        page_type="entity",
        body="# Alice\n\n## Summary\nAlice knows [[ghost-page]].\n",
    )
    app = _make_app(vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/graph")
    body = r.json()
    assert body["ok"] is True
    assert body["edges"] == []
    assert len(body["broken"]) == 1
    assert body["broken"][0]["source"] == "alice"
    assert body["broken"][0]["target"] == "ghost-page"


# ----------------------------------------------------------------------
# /backlinks/{slug}
# ----------------------------------------------------------------------


def test_backlinks_for_harald_includes_ruben_with_snippet(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/backlinks/harald")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "harald"
    backlinks_by_slug = {b["slug"]: b for b in body["backlinks"]}
    assert "ruben" in backlinks_by_slug
    snippet = backlinks_by_slug["ruben"]["snippet"]
    assert "harald" in snippet.lower()


def test_backlinks_for_unreferenced_slug_returns_empty_list(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/backlinks/orphan")
    body = r.json()
    assert body["ok"] is True
    assert body["backlinks"] == []


# ----------------------------------------------------------------------
# /search
# ----------------------------------------------------------------------


def test_search_happy_path_returns_scored_hits(
    populated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The /search route's VaultSearch opens the FTS DB from _default_db_path()
    # and never builds an index itself (the real system indexes via the
    # AtomicWriter on writes / a bootstrap reindex). Point the search at an
    # isolated temp DB and index the populated vault into it, so this test is
    # hermetic instead of depending on the shared real data/jarvis.db.
    import sqlite3

    fts_index = pytest.importorskip("jarvis.memory.wiki.fts_index")
    db = tmp_path / "fts.db"
    monkeypatch.setattr(
        "jarvis.memory.wiki.search._default_db_path", lambda: db
    )
    conn = sqlite3.connect(str(db))
    try:
        fts_index.index_vault(populated_vault, conn)
    finally:
        conn.close()

    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": "pizza"})
    body = r.json()
    assert body["ok"] is True
    assert body["query"] == "pizza"
    assert len(body["hits"]) >= 1
    top_hit = body["hits"][0]
    assert top_hit["slug"] == "ruben"
    assert 0.0 <= top_hit["score"] <= 1.0
    assert top_hit["path"].endswith(".md")
    assert "pizza" in top_hit["snippet"].lower()


def test_search_empty_query_returns_error_envelope(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": ""})
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False
    assert body["error"] == "empty query"


def test_search_with_fts5_syntax_chars_is_sanitised(populated_vault: Path) -> None:
    """Query containing FTS5 special chars must not raise; result OK envelope."""
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get(
            "/api/wiki/search",
            params={"q": 'pizza" AND (ruben*)'},
        )
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["query"] == "pizza AND ruben"


def test_search_k_parameter_caps_results(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": "is", "k": 1})
    body = r.json()
    assert body["ok"] is True
    assert len(body["hits"]) <= 1


# ----------------------------------------------------------------------
# /ingest
# ----------------------------------------------------------------------


def test_ingest_writes_through_shared_curator_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = tmp_path / "vault" / "entities" / "traveler.md"
    curator = _FakeCurator(
        SimpleNamespace(
            applied=[page],
            skipped_due_to_recent_edit=[],
            failed_validation=[],
            blocked_pii=[],
        )
    )
    monkeypatch.setattr(wiki_routes, "get_running_curator", lambda: curator)

    with TestClient(_make_app(tmp_path / "vault")) as client:
        response = client.post(
            "/api/wiki/ingest",
            json={
                "text": "The user will travel to San Francisco tomorrow.",
                "source": "test:explicit",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "source": "test:explicit",
        "applied": 1,
        "skipped_due_to_recent_edit": 0,
        "failed_validation": 0,
        "blocked_sensitive_content": 0,
        "pages_touched": ["traveler.md"],
    }
    assert curator.calls == [
        ("The user will travel to San Francisco tomorrow.", "test:explicit")
    ]


def test_ingest_returns_non_success_when_curator_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    curator = _FakeCurator(
        SimpleNamespace(
            applied=[],
            skipped_due_to_recent_edit=[],
            failed_validation=[],
            blocked_pii=[],
        )
    )
    monkeypatch.setattr(wiki_routes, "get_running_curator", lambda: curator)

    with TestClient(_make_app(tmp_path / "vault")) as client:
        response = client.post(
            "/api/wiki/ingest",
            json={"text": "A complete but non-salient statement."},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "nothing-stored"


def test_ingest_returns_503_without_live_curator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wiki_routes, "get_running_curator", lambda: None)

    with TestClient(_make_app(tmp_path / "vault")) as client:
        response = client.post(
            "/api/wiki/ingest",
            json={"text": "A complete statement for the knowledge Wiki."},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "not-bootstrapped"


def test_ingest_openapi_declares_monitor_risk() -> None:
    operation = _make_app(None).openapi()["paths"]["/api/wiki/ingest"]["post"]
    assert operation["x-jarvis-risk-tier"] == "monitor"


def test_backfill_preview_uses_live_capture_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(
        extractor=object(),
        journal=SimpleNamespace(backlog_count=lambda: 0),
        scheduler=None,
    )
    monkeypatch.setattr(wiki_routes, "get_running_capture_runtime", lambda: runtime)

    async def _fake_backfill(**kwargs):  # noqa: ANN003, ANN202
        assert kwargs["dry_run"] is True
        assert kwargs["days"] == 2
        return _FakeBackfillResult()

    monkeypatch.setattr(
        "jarvis.memory.wiki.backfill.backfill_realtime_sessions",
        _fake_backfill,
    )
    app = _make_app(tmp_path / "vault")
    app.state.session_store = object()
    with TestClient(app) as client:
        response = client.post(
            "/api/wiki/backfill",
            json={"days": 2, "max_sessions": 20, "dry_run": True},
        )
    assert response.status_code == 200
    assert response.json()["sessions_eligible"] == 2
    assert response.json()["consolidation_runs"] == 0


def test_backfill_execute_reports_only_writes_from_this_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal

    journal = CandidateJournal(tmp_path / "data" / "jarvis.db")
    historical_key = "session:v2:historical"
    attempted_key = "session:v2:attempted"
    try:
        assert journal.claim_capture(
            historical_key, "old", "session-sweep", "a" * 64, "historical"
        )
        assert journal.commit_capture_candidates(
            [CandidateFact(fact="An older fact.", evidence_turn_id="old-turn")],
            review_key=historical_key,
            source_label="old",
            turn_hash=historical_key,
        ) == 1
        historical_id = journal.pending()[0].id
        journal.mark(
            [historical_id],
            status="consolidated",
            decision="add",
            target_path="entities/old.md",
        )
        assert journal.claim_capture(
            attempted_key, "new", "session-sweep", "b" * 64, "attempted"
        )
        assert journal.commit_capture_candidates(
            [CandidateFact(fact="A current fact.", evidence_turn_id="new-turn")],
            review_key=attempted_key,
            source_label="new",
            turn_hash=attempted_key,
        ) == 1
        assert journal.append(
            [CandidateFact(fact="An unrelated pending fact.")],
            source_label="unrelated",
            turn_hash="unrelated",
        ) == 1

        class _Scheduler:
            async def trigger(  # noqa: ANN202
                self, _source, *, review_keys  # noqa: ANN001
            ):
                assert tuple(review_keys) == (historical_key, attempted_key)
                pending_id = journal.pending(review_keys=review_keys)[0].id
                journal.mark(
                    [pending_id],
                    status="consolidated",
                    decision="update",
                    target_path="entities/current.md",
                )
                return SimpleNamespace(
                    triggered=True,
                    skip_reason="",
                    curator_output_label="journal-batch:1",
                )

        runtime = SimpleNamespace(
            extractor=object(), journal=journal, scheduler=_Scheduler()
        )
        monkeypatch.setattr(
            wiki_routes, "get_running_capture_runtime", lambda: runtime
        )
        result = SimpleNamespace(
            review_keys=(historical_key, attempted_key),
            attempted_review_keys=(attempted_key,),
            sessions_failed=0,
            sessions_in_progress=0,
            as_dict=lambda: {
                "dry_run": False,
                "days": 2,
                "sessions_scanned": 2,
                "sessions_eligible": 1,
                "sessions_already_reviewed": 1,
                "sessions_in_progress": 0,
                "sessions_reviewed": 1,
                "sessions_failed": 0,
                "turns_considered": 2,
                "candidates_journaled": 1,
            },
        )

        async def _fake_backfill(**_kwargs):  # noqa: ANN003, ANN202
            return result

        monkeypatch.setattr(
            "jarvis.memory.wiki.backfill.backfill_realtime_sessions",
            _fake_backfill,
        )
        app = _make_app(tmp_path / "vault")
        app.state.session_store = object()
        with TestClient(app) as client:
            response = client.post(
                "/api/wiki/backfill",
                json={"days": 2, "max_sessions": 20, "dry_run": False},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["accepted_writes"] == 1
        assert body["stage2"]["add"] == 1
        assert body["stage2"]["update"] == 1
        assert body["journal_backlog"] == 1
        assert [row.fact for row in journal.pending()] == [
            "An unrelated pending fact."
        ]
    finally:
        journal.close()


@pytest.mark.parametrize(
    ("terminal_status", "label", "expected_status", "expected_code"),
    [
        ("rejected", "journal-batch:1", 422, "wiki-backfill-stage2-rejected"),
        ("skipped", "judge-truncated", 503, "wiki-backfill-stage2-skipped"),
    ],
)
def test_backfill_execute_reports_terminal_stage2_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    terminal_status: str,
    label: str,
    expected_status: int,
    expected_code: str,
) -> None:
    from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal

    journal = CandidateJournal(tmp_path / "data" / "jarvis.db")
    key = "session:v2:loss"
    assert journal.claim_capture(key, "loss", "session-sweep", "c" * 64, "loss")
    assert journal.commit_capture_candidates(
        [CandidateFact(fact="A candidate that cannot land.", evidence_turn_id="t1")],
        review_key=key,
        source_label="loss",
        turn_hash=key,
    ) == 1

    class _Scheduler:
        async def trigger(  # noqa: ANN202
            self, _source, *, review_keys  # noqa: ANN001
        ):
            candidate_id = journal.pending(review_keys=review_keys)[0].id
            journal.mark([candidate_id], status=terminal_status)
            return SimpleNamespace(
                triggered=True,
                skip_reason="",
                curator_output_label=label,
            )

    result = SimpleNamespace(
        review_keys=(key,),
        attempted_review_keys=(key,),
        sessions_failed=0,
        sessions_in_progress=0,
        as_dict=lambda: {
            "dry_run": False,
            "days": 2,
            "sessions_scanned": 1,
            "sessions_eligible": 1,
            "sessions_already_reviewed": 0,
            "sessions_in_progress": 0,
            "sessions_reviewed": 1,
            "sessions_failed": 0,
            "turns_considered": 1,
            "candidates_journaled": 1,
        },
    )

    async def _fake_backfill(**_kwargs):  # noqa: ANN003, ANN202
        return result

    runtime = SimpleNamespace(
        extractor=object(), journal=journal, scheduler=_Scheduler()
    )
    monkeypatch.setattr(wiki_routes, "get_running_capture_runtime", lambda: runtime)
    monkeypatch.setattr(
        "jarvis.memory.wiki.backfill.backfill_realtime_sessions", _fake_backfill
    )
    app = _make_app(tmp_path / "vault")
    app.state.session_store = object()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/wiki/backfill",
                json={"days": 2, "max_sessions": 20, "dry_run": False},
            )
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
    finally:
        journal.close()


@pytest.mark.parametrize(
    ("failed", "in_progress", "expected_status", "expected_code"),
    [
        (1, 0, 503, "wiki-backfill-extraction-failed"),
        (0, 1, 409, "wiki-backfill-already-running"),
    ],
)
def test_backfill_execute_fails_closed_on_incomplete_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed: int,
    in_progress: int,
    expected_status: int,
    expected_code: str,
) -> None:
    from jarvis.memory.wiki.journal import CandidateJournal

    journal = CandidateJournal(tmp_path / "data" / "jarvis.db")
    runtime = SimpleNamespace(
        extractor=object(), journal=journal, scheduler=object()
    )
    monkeypatch.setattr(wiki_routes, "get_running_capture_runtime", lambda: runtime)
    result = SimpleNamespace(
        review_keys=(),
        attempted_review_keys=(),
        sessions_failed=failed,
        sessions_in_progress=in_progress,
        as_dict=lambda: {
            "dry_run": False,
            "days": 2,
            "sessions_scanned": 1,
            "sessions_eligible": int(not in_progress),
            "sessions_already_reviewed": 0,
            "sessions_in_progress": in_progress,
            "sessions_reviewed": 0,
            "sessions_failed": failed,
            "turns_considered": 1,
            "candidates_journaled": 0,
        },
    )

    async def _fake_backfill(**_kwargs):  # noqa: ANN003, ANN202
        return result

    monkeypatch.setattr(
        "jarvis.memory.wiki.backfill.backfill_realtime_sessions", _fake_backfill
    )
    app = _make_app(tmp_path / "vault")
    app.state.session_store = object()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/wiki/backfill",
                json={"days": 2, "max_sessions": 20, "dry_run": False},
            )
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
    finally:
        journal.close()


def test_backfill_requires_both_live_stores(tmp_path: Path) -> None:
    app = _make_app(tmp_path / "vault")
    app.state.session_store = None
    with TestClient(app) as client:
        response = client.post("/api/wiki/backfill", json={"dry_run": True})
    assert response.status_code == 503


def test_backfill_openapi_declares_dangerous_ask_risk() -> None:
    operation = _make_app(None).openapi()["paths"]["/api/wiki/backfill"]["post"]
    assert operation["x-jarvis-dangerous"] is True
    assert operation["x-jarvis-risk-tier"] == "ask"


# ----------------------------------------------------------------------
# /reindex
# ----------------------------------------------------------------------


def test_reindex_replaces_stale_rows_with_active_vault(
    populated_vault: Path,
) -> None:
    import sqlite3

    from jarvis.memory.wiki.db_path import resolve_wiki_db_path

    app = _make_app(populated_vault)
    db_path = resolve_wiki_db_path(app.state.config.memory.data_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE wiki_fts USING fts5("
            "path UNINDEXED, title, frontmatter, body, mtime UNINDEXED)"
        )
        conn.execute(
            "INSERT INTO wiki_fts VALUES (?, ?, ?, ?, ?)",
            ("entities/stale.md", "Stale", "", "old", "0"),
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        preview = client.post("/api/wiki/reindex", params={"dry_run": "true"}).json()
        result = client.post("/api/wiki/reindex").json()
        health = client.get("/api/wiki/health").json()["health"]

    assert preview["indexed_before"] == 1
    assert preview["indexed_pages"] == 1
    assert result["ok"] is True
    assert result["indexed_pages"] == 3
    assert health["index_state"] == "ok"
    assert health["indexed_pages"] == health["vault_pages"] == 3
    assert health["missing_pages"] == 0
    assert health["orphaned_pages"] == 0
    assert health["outdated_pages"] == 0
    assert health["last_index_at"] is not None
    assert health["last_index_operation"] == "rebuild"
    assert health["index_lag_seconds"] == 0.0

    conn = sqlite3.connect(str(db_path))
    try:
        paths = {row[0] for row in conn.execute("SELECT path FROM wiki_fts")}
    finally:
        conn.close()
    assert "entities/stale.md" not in paths
    assert "entities/ruben.md" in paths


# ----------------------------------------------------------------------
# Defensive: missing config
# ----------------------------------------------------------------------


def test_tree_without_config_returns_empty_ok(tmp_path: Path) -> None:
    """No ``app.state.config`` at all — must still return shape-correct JSON."""
    app = FastAPI()
    app.include_router(wiki_router)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert body["stats"]["total_pages"] == 0


def test_page_without_config_returns_error_envelope() -> None:
    app = FastAPI()
    app.include_router(wiki_router)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/anything")
    body = r.json()
    assert body["ok"] is False
    assert "not configured" in body["error"]


# ----------------------------------------------------------------------
# /health (spec A5)
# ----------------------------------------------------------------------


def test_health_returns_200_with_fresh_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fresh ``WikiHealth`` singleton reports the all-unknown baseline shape.

    The singleton is process-wide, so other tests in the same run may have
    mutated it — replace it with a brand-new instance for this assertion
    rather than relying on run order for isolation.
    """
    from jarvis.memory.wiki.health import WikiHealth

    monkeypatch.setattr("jarvis.memory.wiki.health.health", WikiHealth())

    app = FastAPI()
    app.include_router(wiki_router)
    app.state.config = SimpleNamespace(
        wiki_integration=SimpleNamespace(vault_root=tmp_path / "vault"),
        memory=SimpleNamespace(data_dir=tmp_path / "data"),
    )
    with TestClient(app) as client:
        r = client.get("/api/wiki/health")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["health"]["journal_backlog"] == 0
    assert body["health"]["bootstrap_ok"] is None
    assert body["health"]["last_write"] is None
    assert body["health"]["last_index"] is None
    assert body["health"]["last_chain_failure"] is None
    assert body["health"]["index_available"] is False
    assert body["health"]["index_state"] == "stale"
    assert body["health"]["index_state_reason"] == "vault_unavailable"
    assert body["health"]["capture_funnel"] == {
        "window_hours": 24,
        "total": 0,
        "started": 0,
        "filtered": 0,
        "empty": 0,
        "candidates": 0,
        "failed": 0,
        "facts": 0,
        "sessions_swept": 0,
        "stage2_pending": 0,
        "stage2_add": 0,
        "stage2_update": 0,
        "stage2_noop": 0,
        "stage2_invalidate": 0,
        "stage2_rejected": 0,
        "stage2_skipped": 0,
        "writes": 0,
    }
    assert body["health"]["capture_error"] is None


def test_health_restores_last_write_and_backlog_from_journal(tmp_path: Path) -> None:
    from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal

    vault = tmp_path / "vault"
    _write_page(
        vault,
        "entities",
        "ruben",
        page_type="entity",
        body="# Ruben\n",
    )
    app = _make_app(vault)
    db_path = app.state.config.memory.data_dir / "jarvis.db"
    journal = CandidateJournal(db_path)
    assert journal.append(
        [CandidateFact(fact="A durable fact")],
        source_label="test-source",
        turn_hash="hash-1",
    ) == 1
    journal.mark(
        [1],
        status="consolidated",
        decision="add",
        target_path="entities/ruben.md",
    )
    assert journal.append(
        [CandidateFact(fact="A pending fact")],
        source_label="test-source",
        turn_hash="hash-2",
    ) == 1
    assert journal.claim_capture(
        "live:v2:s1:t1",
        "realtime:1",
        "realtime",
        "a" * 64,
        "s1",
        "t1",
    )
    assert journal.finish_capture(
        "live:v2:s1:t1",
        "candidates",
        candidate_count=2,
        provider="gemini",
    )

    with TestClient(app) as client:
        body = client.get("/api/wiki/health").json()

    health = body["health"]
    assert health["journal_backlog"] == 1
    assert health["last_write"]["pages"] == ["entities/ruben.md"]
    assert health["vault_pages"] == 1
    assert health["missing_pages"] == 1
    assert health["orphaned_pages"] == 0
    assert health["index_state"] == "stale"
    assert health["capture_funnel"]["total"] == 1
    assert health["capture_funnel"]["candidates"] == 1
    assert health["capture_funnel"]["facts"] == 2
