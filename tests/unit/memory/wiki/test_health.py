"""WikiHealth: silent failures must become visible state (spec A5)."""
import os
import sqlite3

from jarvis.memory.wiki.fts_index import ensure_schema, upsert_page
from jarvis.memory.wiki.health import WikiHealth, inspect_index_health


def test_snapshot_starts_unknown_but_valid():
    h = WikiHealth()
    snap = h.snapshot()
    assert snap["bootstrap_ok"] is None
    assert snap["last_write"] is None
    assert snap["last_index"] is None
    assert snap["journal_backlog"] == 0


def test_record_write_success_and_failure_round_trip():
    h = WikiHealth()
    h.record_write(True, pages=["entities/joy.md"], error=None, source="tool")
    assert h.snapshot()["last_write"]["ok"] is True
    h.record_write(False, pages=[], error="all providers failed", source="tool")
    last = h.snapshot()["last_write"]
    assert last["ok"] is False
    assert "providers" in last["error"]


def test_chain_failure_and_backlog_recorded():
    h = WikiHealth()
    h.record_chain_failure("openai 401; gemini 429")
    h.record_backlog(5)
    snap = h.snapshot()
    assert snap["last_chain_failure"]["detail"].startswith("openai")
    assert snap["journal_backlog"] == 5


def test_chain_success_clears_the_failure_record():
    h = WikiHealth()
    h.record_chain_failure("openai 401; gemini 429")
    assert h.snapshot()["last_chain_failure"] is not None
    h.record_chain_success()
    assert h.snapshot()["last_chain_failure"] is None


def test_chain_failure_detail_redacts_secrets():
    h = WikiHealth()
    secret = "sk-proj-" + "Q" * 32
    h.record_chain_failure(f"openai failed with {secret}")

    detail = h.snapshot()["last_chain_failure"]["detail"]
    assert secret not in detail
    assert "<redacted:openai_key>" in detail


def test_snapshot_is_json_safe():
    import json

    h = WikiHealth()
    h.record_bootstrap(True)
    h.record_write(True, pages=["log.md"], error=None, source="bridge")
    h.record_index(True, operation="upsert", path="entities/joy.md")
    json.dumps(h.snapshot())  # must not raise


def test_index_health_reports_path_and_freshness_drift(tmp_path):
    vault = tmp_path / "vault"
    current = vault / "entities" / "current.md"
    missing = vault / "concepts" / "missing.md"
    orphan = vault / "projects" / "orphan.md"
    for path in (current, missing, orphan):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n", encoding="utf-8")

    db_path = tmp_path / "data" / "jarvis.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        upsert_page(conn, vault, current)
        upsert_page(conn, vault, orphan)
    finally:
        conn.close()

    orphan.unlink()
    future_mtime = current.stat().st_mtime + 10.0
    os.utime(current, (future_mtime, future_mtime))

    snapshot = inspect_index_health(vault, db_path)

    assert snapshot["index_available"] is True
    assert snapshot["vault_pages"] == 2
    assert snapshot["indexed_pages"] == 2
    assert snapshot["missing_pages"] == 1
    assert snapshot["orphaned_pages"] == 1
    assert snapshot["outdated_pages"] == 1
    assert snapshot["index_state"] == "stale"
    assert snapshot["index_state_reasons"] == [
        "missing_pages",
        "orphaned_pages",
        "outdated_pages",
    ]
    assert snapshot["last_index_at"] is not None
    assert snapshot["index_lag_seconds"] > 0


def test_empty_vault_with_missing_index_is_not_reported_healthy(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    snapshot = inspect_index_health(vault, tmp_path / "missing.db")

    assert snapshot["vault_pages"] == 0
    assert snapshot["index_available"] is False
    assert snapshot["index_state"] == "stale"
    assert snapshot["index_state_reason"] == "index_unavailable"
