"""Tests for the single-source .setup-complete marker helpers."""
from pathlib import Path

from jarvis.setup import state as st


def test_marker_path_is_sibling_of_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "setup_state.json"
    assert st.setup_complete_marker_path(state_file) == tmp_path / ".setup-complete"


def test_marker_exists_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "setup_state.json"
    assert st.setup_complete_marker_exists(state_file) is False
    (tmp_path / ".setup-complete").write_text("done\n", encoding="utf-8")
    assert st.setup_complete_marker_exists(state_file) is True


def test_default_marker_matches_config_data_dir() -> None:
    from jarvis.core.config import DATA_DIR

    assert st.setup_complete_marker_path() == DATA_DIR / ".setup-complete"
