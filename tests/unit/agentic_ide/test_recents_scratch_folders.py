"""The recent-folder list holds folders a person chose, and nothing else.

This is the wizard's front page. When it fills up with folders the user never
opened, the list stops being a shortcut and becomes noise they have to read
past — and that is not hypothetical: an automated run once left seven scratch
folders there, pushing the one real project to the bottom.

The rule is enforced on the way in AND on the way out, because a store polluted
before the rule existed must clean itself up rather than wait for someone to
notice.

**Note on the fixture.** pytest hands out its temporary directories from inside
the system temp tree, so a naive test would see its own "project" classified as
scratch — correctly. The scratch boundary is therefore moved to a controlled
sub-directory for the tests that need a project on the other side of it, and
the real boundary is asserted separately.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jarvis.agentic_ide import recents

# Captured at import, before any fixture can replace it. The root conftest
# stands the scratch rule down for the whole suite (pytest's own temporary
# folders live inside the system temp tree, so the rule would otherwise fire on
# every test that opens a workspace) — the tests below that assert the REAL
# boundary put the genuine implementation back.
_REAL_TEMP_ROOTS = recents._temp_roots


@pytest.fixture
def real_temp_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recents, "_temp_roots", _REAL_TEMP_ROOTS)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: target)
    return target


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch tree, with everything else in ``tmp_path`` outside it."""
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(recents, "_temp_roots", lambda: [root.resolve()])
    return root


def _folder(parent: Path, name: str) -> Path:
    made = parent / name
    made.mkdir(parents=True, exist_ok=True)
    return made


def test_a_scratch_folder_is_never_remembered(tmp_path: Path, store: Path, scratch: Path) -> None:
    project = _folder(tmp_path, "webshop")
    throwaway = _folder(scratch, "test_resuming_counts_conversat0")

    recents.remember(str(project), terminals=2, agents={"claude": 2})
    recents.remember(str(throwaway), terminals=1, agents={"codex": 1})

    assert [r.path for r in recents.load()] == [str(project)]


def test_an_already_polluted_store_reads_clean(tmp_path: Path, store: Path, scratch: Path) -> None:
    """The file on disk is the one yesterday's bug left behind; the list is not."""
    project = _folder(tmp_path, "webshop")
    throwaway = _folder(scratch, "test_resuming_reopens_the_work0")
    store.write_text(
        json.dumps(
            [
                {"path": str(throwaway), "name": throwaway.name, "last_used": 99.0},
                {"path": str(project), "name": "webshop", "last_used": 1.0},
            ]
        ),
        encoding="utf-8",
    )

    assert [r.path for r in recents.load()] == [str(project)]


def test_the_next_write_removes_the_pollution_from_disk(
    tmp_path: Path, store: Path, scratch: Path
) -> None:
    project = _folder(tmp_path, "webshop")
    throwaway = _folder(scratch, "test_resuming_starts_no_agent_0")
    store.write_text(
        json.dumps([{"path": str(throwaway), "name": throwaway.name, "last_used": 99.0}]),
        encoding="utf-8",
    )

    recents.remember(str(project), terminals=1, agents={"claude": 1})

    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert [entry["path"] for entry in on_disk] == [str(project)]


@pytest.mark.usefixtures("real_temp_rule")
def test_the_real_system_temp_directory_is_the_boundary(tmp_path: Path) -> None:
    """Asserted against the machine's actual temp dir, not a stand-in.

    ``tmp_path`` lives inside it, which is precisely why the automated runs
    polluted the store in the first place.
    """
    assert recents.is_throwaway(tempfile.gettempdir()) is True
    assert recents.is_throwaway(str(tmp_path)) is True


@pytest.mark.usefixtures("real_temp_rule")
def test_the_real_rule_leaves_a_home_directory_alone() -> None:
    assert recents.is_throwaway(str(Path.home())) is False


def test_real_work_is_not_mistaken_for_scratch(scratch: Path) -> None:
    """The filter must not reach past the temp tree into somebody's projects.

    A folder merely NAMED like scratch space stays remembered — the rule is
    about where a folder lives, not what it is called.
    """
    assert recents.is_throwaway(str(Path.home())) is False
    assert recents.is_throwaway(str(Path.home() / "projects" / "tmp")) is False
    assert recents.is_throwaway(str(scratch.parent / "temp-invoices")) is False


@pytest.mark.usefixtures("real_temp_rule")
def test_an_unreadable_path_is_not_treated_as_scratch() -> None:
    """Refusing to remember is the exception; a malformed entry is not evidence
    of one, so an unresolvable path falls through to being kept."""
    assert recents.is_throwaway("\x00not-a-path") is False
