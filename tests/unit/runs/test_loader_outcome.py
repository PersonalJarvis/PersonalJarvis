"""RunLoader must attach outcome (success/partial/failed) + activity to runs."""
from jarvis.clis.usage_log import UsageLog
from jarvis.runs.loader import RunLoader
from jarvis.sessions.store import SessionStore


def _loader(store, tmp_path):
    return RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                     missions_lookup=None)


def test_failed_cu_action_makes_run_partial_with_activity(tmp_path):
    store = SessionStore(tmp_path / "chats.db")
    store.open()
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1000)
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1010, kind="ResponseGenerated",
                       payload={"text": "Ich versuche es."})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1020, kind="ActionProposed",
                       payload={"tool_name": "open_app", "risk_tier": "safe"})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1030, kind="ActionExecuted",
                       payload={"tool_name": "open_app", "success": False, "error": "not found"})
    store.finalize_turn(turn_id="t1", ended_ms=1500, user_text="öffne X", user_lang="de",
                        jarvis_text="Ich versuche es.", jarvis_lang="de", tier="deep",
                        provider="gemini", model="flash", tokens_in=5, tokens_out=3,
                        cost_usd=0.0, latency_total_ms=100, tool_calls=["open_app"],
                        think_ms=0, speak_ms=0)
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=1, total_cost_usd=0.0, total_tokens_in=5,
                           total_tokens_out=3, providers_used=["gemini"])

    run = _loader(store, tmp_path).load_run("s1")
    assert run.outcome == "partial"           # answered, but a tool failed
    assert run.turns[0].outcome == "partial"
    # open_app is a Computer-Use action → surfaced as the CU agent, not a raw tool.
    assert "computer_use" in run.activity.agents
    assert "open_app" not in run.activity.tools
    # PER-TURN activity: each turn carries what IT triggered, not just the run total.
    assert "computer_use" in run.turns[0].activity.agents
    store.close()


def test_list_item_outcome_and_feature_tags(tmp_path):
    store = SessionStore(tmp_path / "chats.db")
    store.open()
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1000)
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1010, kind="ResponseGenerated",
                       payload={"text": "Alles gut."})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1015, kind="LatencySpan",
                       payload={"phase": "brain_first_token", "duration_ms": 22000.0})
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=1, total_cost_usd=0.0, total_tokens_in=0,
                           total_tokens_out=0, providers_used=["gemini"])

    items = _loader(store, tmp_path).list_runs(limit=10)
    item = next(i for i in items if i.session_id == "s1")
    # Slow but successful: SLO breaches, yet the OUTCOME is success (green).
    assert item.outcome == "success"
    assert item.slo_status == "breach"
    store.close()
