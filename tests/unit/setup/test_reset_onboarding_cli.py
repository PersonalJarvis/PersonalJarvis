from jarvis import __main__ as m
from jarvis.setup import state as st


def test_reset_onboarding_clears_markers_and_keeps_foreign(tmp_path, monkeypatch):
    state_file = tmp_path / "setup_state.json"
    setup_complete = tmp_path / ".setup-complete"
    setup_complete.write_text("done", encoding="utf-8")

    # A foreign (non-onboarding) flag that must survive the reset.
    st._merge_state({"obsidian_setup_seen_at": "2026-01-01T00:00:00+00:00"}, state_file)
    st.mark_onboarding_complete(state_file)
    assert st.is_onboarding_complete(state_file) is True

    monkeypatch.setattr(m.cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(m, "_ONBOARDING_STATE_PATH", state_file, raising=False)

    rc = m._cmd_reset_onboarding()

    assert rc == 0
    assert st.is_onboarding_complete(state_file) is False
    assert not setup_complete.exists()
    # Foreign key preserved.
    assert st.load_setup_state(state_file)["obsidian_setup_seen_at"] == "2026-01-01T00:00:00+00:00"
