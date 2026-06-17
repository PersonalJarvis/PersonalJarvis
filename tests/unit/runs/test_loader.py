from jarvis.runs.loader import RunLoader
from jarvis.sessions.store import SessionStore
from jarvis.clis.usage_log import UsageLog


def _store(tmp_path):
    s = SessionStore(tmp_path / "chats.db")
    s.open()
    return s


def test_load_run_assembles_turns_and_analytics(tmp_path):
    store = _store(tmp_path)
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1000)
    store.finalize_turn(
        turn_id="t1", ended_ms=1500, user_text="hi", user_lang="en",
        jarvis_text="hello", jarvis_lang="en", tier="router", provider="claude-api",
        model="opus", tokens_in=10, tokens_out=5, cost_usd=0.01,
        latency_total_ms=500, tool_calls=["cli_gcloud"], think_ms=200, speak_ms=300,
    )
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1100, kind="LatencySpan",
                       payload={"phase": "intent_decision", "duration_ms": 200.0})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1200, kind="ActionApproved",
                       payload={"tool_name": "cli_gcloud", "approved_by": "whitelist"})
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=1, total_cost_usd=0.01, total_tokens_in=10,
                           total_tokens_out=5, providers_used=["claude-api"])

    usage = UsageLog(db_path=tmp_path / "u.db")
    loader = RunLoader(session_store=store, usage_log=usage, missions_lookup=None)

    run = loader.load_run("s1")
    assert run is not None
    assert run.session.id == "s1"
    assert len(run.turns) == 1
    turn = run.turns[0]
    assert turn.latency and turn.latency[0].slo_status == "breach"
    assert any(s.kind == "risk" for s in turn.decision_path)
    assert run.analytics.cost_by_provider["claude-api"] == 0.01
    store.close(); usage.close()


def test_list_runs_maps_headers(tmp_path):
    store = _store(tmp_path)
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=0, total_cost_usd=0.0, total_tokens_in=0,
                           total_tokens_out=0, providers_used=[])
    loader = RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                       missions_lookup=None)
    items = loader.list_runs(limit=10)
    assert items and items[0].session_id == "s1"
    assert items[0].wake_source == "voice"  # default; hotkey/channel only if known
    store.close()


def test_load_run_unknown_returns_none(tmp_path):
    store = _store(tmp_path)
    loader = RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                       missions_lookup=None)
    assert loader.load_run("nope") is None
    store.close()
