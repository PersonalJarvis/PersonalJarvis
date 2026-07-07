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


def test_write_and_remove_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "setup_state.json"
    st.write_setup_complete_marker("Setup completed on Python 3.11\n", state_file)
    assert st.setup_complete_marker_exists(state_file) is True
    assert st.remove_setup_complete_marker(state_file) is True
    assert st.setup_complete_marker_exists(state_file) is False
    assert st.remove_setup_complete_marker(state_file) is False  # already gone


def test_no_caller_rebuilds_the_marker_path() -> None:
    """Read/write/delete must all route through jarvis.setup.state — a literal
    '.setup-complete' STRING (a rebuilt path) anywhere else can silently desync
    and revive the 'onboarding reappears every restart' bug class. Prose
    mentions in docstrings/log messages are fine; quoted literals are not."""
    import re
    from pathlib import Path as P

    literal = re.compile(r"""['"]\.setup-complete['"]""")
    repo = P(__file__).resolve().parents[3]
    offenders = []
    for py in (repo / "jarvis").rglob("*.py"):
        if py.name == "state.py" and py.parent.name == "setup":
            continue
        if literal.search(py.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(py.relative_to(repo)))
    assert offenders == [], f"rebuilt marker path outside jarvis/setup/state.py: {offenders}"
