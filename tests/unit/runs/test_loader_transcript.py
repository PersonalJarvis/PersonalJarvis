"""RunLoader must attach the gap-less transcript to each assembled turn."""
from jarvis.clis.usage_log import UsageLog
from jarvis.runs.loader import RunLoader
from jarvis.sessions.store import SessionStore


def test_loaded_turn_carries_transcript(tmp_path):
    store = SessionStore(tmp_path / "chats.db")
    store.open()
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1000)
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1010, kind="TranscriptFinal",
                       payload={"text": "Was geht ab?"})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1050, kind="ResponseGenerated",
                       payload={"text": "Alles bestens, Boss."})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1080, kind="SpeechSpoken",
                       payload={"text": "Das hat nicht geklappt.", "spoken_kind": "cu_failure",
                                "detail": "exit 5 - harness reported failure"})
    store.finalize_turn(turn_id="t1", ended_ms=1500, user_text="Was geht ab?", user_lang="de",
                        jarvis_text="Alles bestens, Boss.", jarvis_lang="de", tier="fast",
                        provider="gemini", model="flash", tokens_in=1, tokens_out=1,
                        cost_usd=0.0, latency_total_ms=100, tool_calls=[], think_ms=0, speak_ms=0)
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=1, total_cost_usd=0.0, total_tokens_in=1,
                           total_tokens_out=1, providers_used=["gemini"])

    loader = RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                       missions_lookup=None)
    run = loader.load_run("s1")
    assert run is not None
    transcript = run.turns[0].transcript
    roles = [line.role for line in transcript]
    texts = [line.text for line in transcript]
    assert "user" in roles and "jarvis" in roles and "system" in roles
    assert "Was geht ab?" in texts          # full user utterance
    assert "Alles bestens, Boss." in texts  # the brain reply
    assert any("exit 5" in t for t in texts)  # the non-spoken system diagnostic
    store.close()
