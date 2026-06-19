from jarvis.setup import state as st


def test_default_state_path_is_cwd_independent(monkeypatch, tmp_path):
    """The default onboarding state path must not depend on os.getcwd().

    Regression: the desktop app does NOT always launch with the repo root as its
    CWD — the autostart Scheduled Task sets it, but a manual start / restart-app
    inherits the user home (observed CWD: C:\\Users\\<user>). A CWD-relative
    ``data/setup_state.json`` therefore resolved to a *different* file per start
    method, so the "onboarding complete" flag written by one start was invisible
    to the next, and the first-run setup guide re-appeared on every restart.
    The path must resolve to the same absolute location regardless of CWD.
    """
    from_here = st.state_path().resolve()
    monkeypatch.chdir(tmp_path)
    from_elsewhere = st.state_path().resolve()

    assert from_here == from_elsewhere, (
        "onboarding state path must not depend on the current working directory"
    )
    assert from_here.is_absolute()
    # It must live next to the rest of the app's persistent data (config.DATA_DIR),
    # anchored to the installed package — not to wherever the process was started.
    from jarvis.core import config

    assert from_here == (config.DATA_DIR / "setup_state.json").resolve()


def test_onboarding_roundtrip(tmp_path):
    p = tmp_path / "setup_state.json"

    # Fresh state: everything empty/None.
    assert st.is_onboarding_complete(p) is False
    fresh = st.get_onboarding_state(p)
    assert fresh["completed_at"] is None
    assert fresh["current_step"] is None
    assert fresh["skipped_steps"] == []
    assert fresh["terms_version"] is None
    assert fresh["wake_word_acknowledged_at"] is None

    # Record progress.
    st.set_onboarding_step("wake-word", skipped=["api-keys"], path=p)
    st.accept_terms("1.0", path=p)
    st.acknowledge_wake_word(p)
    mid = st.get_onboarding_state(p)
    assert mid["current_step"] == "wake-word"
    assert mid["skipped_steps"] == ["api-keys"]
    assert mid["terms_version"] == "1.0"
    assert isinstance(mid["terms_accepted_at"], str) and mid["terms_accepted_at"]
    assert isinstance(mid["wake_word_acknowledged_at"], str)

    # Complete.
    st.mark_onboarding_complete(p)
    assert st.is_onboarding_complete(p) is True

    # A foreign key written via the shared merge writer is preserved.
    st._merge_state({"obsidian_setup_seen_at": "2026-01-01T00:00:00+00:00"}, p)
    st.set_onboarding_step("finish", path=p)
    assert st.load_setup_state(p)["obsidian_setup_seen_at"] == "2026-01-01T00:00:00+00:00"

    # Skip-set round-trips through the store.
    st.set_onboarding_step("finish", skipped=["api-keys", "mic-test"], path=p)
    assert st.get_onboarding_state(p)["skipped_steps"] == ["api-keys", "mic-test"]

    # Reset clears only onboarding keys, keeps the foreign key.
    removed = st.reset_onboarding(p)
    assert "onboarding_completed_at" in removed
    after = st.get_onboarding_state(p)
    assert after["completed_at"] is None
    assert st.load_setup_state(p)["obsidian_setup_seen_at"] == "2026-01-01T00:00:00+00:00"
