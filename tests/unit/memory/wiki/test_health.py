"""WikiHealth: silent failures must become visible state (spec A5)."""
from jarvis.memory.wiki.health import WikiHealth


def test_snapshot_starts_unknown_but_valid():
    h = WikiHealth()
    snap = h.snapshot()
    assert snap["bootstrap_ok"] is None
    assert snap["last_write"] is None
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


def test_snapshot_is_json_safe():
    import json

    h = WikiHealth()
    h.record_bootstrap(True)
    h.record_write(True, pages=["log.md"], error=None, source="bridge")
    json.dumps(h.snapshot())  # must not raise
