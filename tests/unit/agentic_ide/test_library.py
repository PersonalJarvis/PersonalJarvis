"""The project/chat library — identity, ordering, isolation and damage limits.

Every test here is about a property the sidebar depends on rather than about a
setter working. The store is the thing that has to still make sense after a
crash, a half-written file, a moved folder and a machine the user has never run
this on before.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.agentic_ide import library


def test_the_same_folder_is_always_the_same_project(tmp_path: Path) -> None:
    """Identity comes from the path, so a lost store does not lose the project.

    This is what makes the library safe to rebuild from recents: the id is
    derived, not minted, so the rebuilt entry is the SAME project rather than a
    duplicate sitting next to the original.
    """
    folder = tmp_path / "repo"
    folder.mkdir()

    first = library.ensure_project(folder)
    second = library.ensure_project(folder)

    assert first.id == second.id
    assert library.project_id_for(folder) == first.id
    assert len(library.list_projects()) == 1


def test_reopening_a_project_keeps_its_name(tmp_path: Path) -> None:
    """A rename survives the next open — otherwise naming a project is pointless."""
    folder = tmp_path / "repo"
    folder.mkdir()
    project = library.ensure_project(folder)
    library.update_project(project.id, name="The good one")

    reopened = library.ensure_project(folder)

    assert reopened.name == "The good one"


def test_projects_list_pinned_first_then_most_recent(tmp_path: Path) -> None:
    """The order IS the sidebar's order, so it is asserted rather than assumed."""
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / name).mkdir()
    alpha = library.ensure_project(tmp_path / "alpha")
    library.ensure_project(tmp_path / "beta")
    gamma = library.ensure_project(tmp_path / "gamma")
    library.update_project(alpha.id, pinned=True)

    listed = [p.name for p in library.list_projects()]

    # Pinned wins outright; the rest fall in most-recently-opened order, and
    # gamma was opened last.
    assert listed[0] == "alpha"
    assert listed[1] == "gamma"
    assert gamma.id in {p.id for p in library.list_projects()}


def test_archived_projects_are_hidden_but_not_gone(tmp_path: Path) -> None:
    """Archiving is a filter, not a delete — that distinction is the whole point."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    library.update_project(project.id, archived=True)

    assert library.list_projects() == []
    assert [p.id for p in library.list_projects(include_archived=True)] == [project.id]


def test_a_vanished_folder_keeps_its_chats(tmp_path: Path) -> None:
    """An unplugged drive must not delete somebody's history.

    The folder is gone from disk and the project is still listed. Reporting it
    as unreachable is the route's job; removing it is nobody's but the user's.
    """
    folder = tmp_path / "repo"
    folder.mkdir()
    project = library.ensure_project(folder)
    library.create_thread(project.id, agent="claude")
    folder.rmdir()

    assert [p.id for p in library.list_projects()] == [project.id]
    assert len(library.list_threads(project.id)) == 1


def test_threads_are_isolated_per_project(tmp_path: Path) -> None:
    """One project's chats never appear under another's, and never load with it."""
    for name in ("left", "right"):
        (tmp_path / name).mkdir()
    left = library.ensure_project(tmp_path / "left")
    right = library.ensure_project(tmp_path / "right")
    library.create_thread(left.id, agent="claude", title="left chat")

    assert [t.title for t in library.list_threads(left.id)] == ["left chat"]
    assert library.list_threads(right.id) == []


def test_a_new_chat_is_untitled(tmp_path: Path) -> None:
    """A chat earns its title from the first prompt.

    Storing a placeholder would put a plausible-looking entry in the list for a
    conversation that never happened, and would then need translating too.
    """
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")

    thread = library.create_thread(project.id, agent="claude")

    assert thread.title == ""
    assert thread.prompts_sent == 0


def test_update_thread_ignores_keys_it_was_not_given(tmp_path: Path) -> None:
    """The allowlist is what keeps ``**changes`` from being a write-anything hole."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="claude")

    updated = library.update_thread(
        project.id, thread.id, title="Fix the wake path", id="hijacked", project_id="x"
    )

    assert updated is not None
    assert updated.title == "Fix the wake path"
    assert updated.id == thread.id
    assert updated.project_id == project.id


def test_a_preview_is_collapsed_and_bounded(tmp_path: Path) -> None:
    """The list carries a subtitle, not a transcript."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="claude")

    updated = library.update_thread(project.id, thread.id, preview="a\n\n  long   " + "x" * 400)

    assert updated is not None
    assert "\n" not in updated.preview
    assert len(updated.preview) <= library.PREVIEW_MAX


def test_deleting_a_project_takes_its_chats_with_it(tmp_path: Path) -> None:
    """A surviving thread file would resurrect deleted chats on the next open."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    library.create_thread(project.id, agent="claude")

    assert library.delete_project(project.id) is True

    reopened = library.ensure_project(tmp_path / "repo")
    assert reopened.id == project.id
    assert library.list_threads(reopened.id) == []


def test_a_damaged_thread_file_costs_one_project(tmp_path: Path) -> None:
    """Damage is bounded to the project it happened in.

    One file per project is the reason this holds, and it is the reason the
    layout was chosen — so it is pinned here rather than left as a comment.
    """
    for name in ("broken", "fine"):
        (tmp_path / name).mkdir()
    broken = library.ensure_project(tmp_path / "broken")
    fine = library.ensure_project(tmp_path / "fine")
    library.create_thread(broken.id, agent="claude")
    library.create_thread(fine.id, agent="codex", title="still here")

    library._threads_path(broken.id).write_text("{ truncated", encoding="utf-8")

    assert library.list_threads(broken.id) == []
    assert [t.title for t in library.list_threads(fine.id)] == ["still here"]


def test_a_future_schema_reads_as_empty(tmp_path: Path) -> None:
    """Half-understanding a newer build's file is worse than rebuilding."""
    (tmp_path / "repo").mkdir()
    library.ensure_project(tmp_path / "repo")

    path = library._projects_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = library.SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert library.list_projects() == []


def test_find_thread_works_without_the_project(tmp_path: Path) -> None:
    """Some callers hold only a chat id — a notification, a rebinding pane."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="codex")

    found = library.find_thread(thread.id)

    assert found is not None
    assert found.project_id == project.id
    assert library.find_thread("nope") is None


def test_title_from_prompt_takes_the_first_line_and_stays_short() -> None:
    """Titles are derived locally — no model call on a path the user waits on."""
    assert library.title_from_prompt("Fix the wake word\n\nmore detail") == ("Fix the wake word")

    long = library.title_from_prompt(
        "Rewrite the entire authentication layer so that every provider degrades"
    )
    assert long.endswith("…")
    assert len(long) < 60
    # Cut on a word boundary, not mid-word.
    assert not long.rstrip("…").endswith(" ")


def test_title_from_prompt_survives_an_empty_prompt() -> None:
    """Whitespace in, empty out — never an exception on the prompt path."""
    assert library.title_from_prompt("   \n\n  ") == ""
