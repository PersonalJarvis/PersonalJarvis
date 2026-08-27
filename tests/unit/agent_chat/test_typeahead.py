"""The composer's typeahead — what "/", "@" and "$" list on each seat.

Every row must come from the disk the seat's runner reads: the folder's own
``.claude`` / ``.agents`` trees, the account's config dir, the installed
plugins, the file list git would show. Nothing typed by hand, nothing from a
seat that would not honour the gesture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import typeahead
from jarvis.agent_chat.runner_brain import explicit_skill
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.ui.web.agent_chat_routes import router


def _skill(root: Path, name: str, description: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n", encoding="utf-8"
    )


def _md(path: Path, description: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"---\ndescription: {description}\n---\nbody\n" if description else "body\n"
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Claude Code config dir standing in for the seat's account."""
    home = tmp_path / "account"
    home.mkdir()
    monkeypatch.setattr(typeahead, "claude_config_dir", lambda: home)
    return home


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    return cwd


@pytest.fixture(autouse=True)
def _fresh_walk_cache():
    typeahead.forget_folder()
    yield
    typeahead.forget_folder()


# ------------------------------------------------------------- triggers


def test_every_runner_the_catalog_can_resolve_has_a_trigger_row():
    """The route derives the composer's triggers from the runner; an unlisted
    runner would silently get no list at all."""
    for runner in ("claude-cli", "codex-cli", "agy-cli", "grok-cli", "api", "brain"):
        assert runner in typeahead.TRIGGERS_BY_RUNNER, runner
    assert typeahead.triggers_for("claude-cli") == ("/", "@")
    assert typeahead.triggers_for("codex-cli") == ("@", "$")
    assert typeahead.triggers_for("brain") == ("/",)
    assert typeahead.triggers_for("nope") == ()


def test_a_trigger_the_seat_does_not_honour_answers_empty(folder: Path):
    assert typeahead.suggest(runner="api", cwd=folder, trigger="/") == {
        "trigger": "/",
        "items": [],
        "truncated": False,
    }
    assert typeahead.suggest(runner="claude-cli", cwd=folder, trigger="$")["items"] == []


# --------------------------------------------------------- claude slash


def test_claude_slash_lists_project_then_account_then_plugins(account: Path, folder: Path):
    _skill(folder / ".claude" / "skills", "deploy", "Ship it")
    _skill(folder / ".agents" / "skills", "deploy", "the mirrored twin — one row, not two")
    _md(folder / ".claude" / "commands" / "review.md", "Review the diff")
    _md(folder / ".claude" / "commands" / "README.md")  # never a command
    _skill(account / "skills", "notes", "Write notes")
    _md(account / "commands" / "standup.md")

    install = account / "plugins" / "cache" / "market" / "github" / "abc"
    _skill(install / "skills", "issue", "Open an issue")
    _md(install / "commands" / "pr.md", "Open a PR")
    off = account / "plugins" / "cache" / "market" / "dark" / "abc"
    _skill(off / "skills", "hidden", "switched off")
    (account / "plugins").mkdir(exist_ok=True)
    (account / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "github@market": [{"scope": "user", "installPath": str(install)}],
                    "dark@market": [{"scope": "user", "installPath": str(off)}],
                    "gone@market": [{"scope": "user", "installPath": str(account / "nope")}],
                },
            }
        ),
        encoding="utf-8",
    )
    (account / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"github@market": True, "dark@market": False}}),
        encoding="utf-8",
    )

    rows = typeahead.claude_slash(folder)
    assert [(r.value, r.group, r.kind) for r in rows] == [
        ("deploy", "project", "skill"),
        ("review", "project", "command"),
        ("notes", "account", "skill"),
        ("standup", "account", "command"),
        ("github:issue", "plugins", "skill"),
        ("github:pr", "plugins", "command"),
    ]
    by_value = {r.value: r for r in rows}
    assert by_value["deploy"].hint == "Ship it"  # the project's own, not the twin's
    assert by_value["github:pr"].hint == "Open a PR"


def test_frontmatter_reads_quoted_and_folded_descriptions(tmp_path: Path):
    quoted = tmp_path / "q.md"
    quoted.write_text('---\nname: "x"\ndescription: "Use when: things"\n---\n', encoding="utf-8")
    folded = tmp_path / "f.md"
    folded.write_text(
        "---\ndescription: >\n  Line one\n  line two.\nname: y\n---\n", encoding="utf-8"
    )
    plain = tmp_path / "p.md"
    plain.write_text("# no frontmatter\n", encoding="utf-8")
    assert typeahead._frontmatter(quoted) == {"name": "x", "description": "Use when: things"}
    assert typeahead._frontmatter(folded) == {"description": "Line one line two.", "name": "y"}
    assert typeahead._frontmatter(plain) == {}


def test_slash_query_ranks_prefix_before_substring_before_hint(account: Path, folder: Path):
    _skill(folder / ".claude" / "skills", "commit", "Commit the work")
    _skill(folder / ".claude" / "skills", "autocommit", "Commit on a timer")
    _skill(folder / ".claude" / "skills", "release", "Tag and commit a version")
    _skill(folder / ".claude" / "skills", "deploy", "Ship it")
    items = typeahead.suggest(runner="claude-cli", cwd=folder, trigger="/", query="commit")["items"]
    assert [i["value"] for i in items] == ["commit", "autocommit", "release"]


# ------------------------------------------------------------- agents


def test_mention_on_claude_lists_agents_before_files(account: Path, folder: Path):
    _md(folder / ".claude" / "agents" / "reviewer.md", "Reviews code")
    _md(folder / ".claude" / "agents" / "INDEX.md")
    _md(account / "agents" / "planner.md")
    (folder / "reviewer_notes.txt").write_text("x", encoding="utf-8")

    rows = typeahead.suggest(runner="claude-cli", cwd=folder, trigger="@", query="review")["items"]
    assert rows[0] == {
        "value": "reviewer",
        "label": "reviewer",
        "hint": "Reviews code",
        "kind": "agent",
        "group": "agents",
    }
    # The definition file itself is a file of the folder too — nearer names first.
    assert [r["value"] for r in rows[1:]] == ["reviewer_notes.txt", ".claude/agents/reviewer.md"]
    everything = typeahead.suggest(runner="claude-cli", cwd=folder, trigger="@")["items"]
    assert [r["value"] for r in everything if r["kind"] == "agent"] == ["reviewer", "planner"]


# -------------------------------------------------------------- codex $


def test_codex_skill_refs_read_the_folder_then_codex_home(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "codex-home"
    monkeypatch.setattr(typeahead, "codex_home", lambda: home)
    _skill(folder / ".agents" / "skills", "grill-me", "Interview me")
    _skill(home / "skills", "design-md", "Design doc")
    _skill(home / "skills" / "cloud", "run-basics", "One level down is fine too")
    rows = typeahead.suggest(runner="codex-cli", cwd=folder, trigger="$")["items"]
    assert [(r["value"], r["group"]) for r in rows] == [
        ("grill-me", "project"),
        ("design-md", "account"),
        ("run-basics", "account"),
    ]


# --------------------------------------------------------------- files


def test_file_walk_skips_build_trees_and_ranks_names_first(folder: Path):
    (folder / "src").mkdir()
    (folder / "src" / "app.py").write_text("x", encoding="utf-8")
    (folder / "src" / "happy.py").write_text("x", encoding="utf-8")
    (folder / "vendor" / "x").mkdir(parents=True)
    (folder / "vendor" / "x" / "app.py").write_text("x", encoding="utf-8")
    (folder / "node_modules" / "m").mkdir(parents=True)
    (folder / "node_modules" / "m" / "app.py").write_text("x", encoding="utf-8")
    (folder / ".secret").write_text("x", encoding="utf-8")
    (folder / "README.md").write_text("x", encoding="utf-8")

    top = [i.value for i in typeahead.file_suggestions(folder, "")]
    assert top[:3] == ["src/", "vendor/", "README.md"]  # folders first, then files, near first
    assert not any("node_modules" in v or ".secret" in v for v in top)

    ranked = [i.value for i in typeahead.file_suggestions(folder, "app")]
    assert ranked == ["src/app.py", "vendor/x/app.py", "src/happy.py"]

    row = typeahead.file_suggestions(folder, "happy")[0]
    assert (row.label, row.hint, row.kind, row.group) == ("happy.py", "src", "file", "files")
    assert typeahead.file_suggestions(folder, "src")[0].kind == "folder"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_checkout_lists_what_git_would_show(folder: Path):
    """Tracked plus untracked-but-not-ignored: build output never appears."""
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    (folder / ".gitignore").write_text("build/\n", encoding="utf-8")
    (folder / "src").mkdir()
    (folder / "src" / "main.py").write_text("x", encoding="utf-8")
    (folder / "build").mkdir()
    (folder / "build" / "out.js").write_text("x", encoding="utf-8")
    (folder / "untracked.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "src"], cwd=folder, check=True)

    listed = typeahead._git_entries(folder)
    assert listed is not None
    entries, truncated = listed
    assert truncated is False
    paths = [e.path for e in entries]
    assert "src/" in paths and "src/main.py" in paths and "untracked.txt" in paths
    assert not any(p.startswith("build") for p in paths)
    assert paths.index("src/") < paths.index("src/main.py")


def test_the_walk_is_cached_per_folder(folder: Path, monkeypatch: pytest.MonkeyPatch):
    (folder / "a.txt").write_text("x", encoding="utf-8")
    assert [i.value for i in typeahead.file_suggestions(folder, "")] == ["a.txt"]
    (folder / "b.txt").write_text("x", encoding="utf-8")
    assert [i.value for i in typeahead.file_suggestions(folder, "")] == ["a.txt"]  # still cached
    typeahead.forget_folder(folder)
    assert [i.value for i in typeahead.file_suggestions(folder, "")] == ["a.txt", "b.txt"]


# -------------------------------------------------------------- jarvis /


def test_jarvis_surface_lists_the_registry_by_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class _FM:
        description = "Morning briefing"

    class _Skill:
        name = "Morning Routine"
        path = tmp_path / "morning-routine" / "SKILL.md"
        frontmatter = _FM()

    class _Registry:
        def list_active(self):
            return [_Skill()]

    class _Ctx:
        registry = _Registry()

    import jarvis.skills.skill_context as skill_context

    monkeypatch.setattr(skill_context, "try_get_skill_context", lambda: _Ctx())
    rows = typeahead.suggest(runner="brain", cwd=None, trigger="/")["items"]
    assert rows == [
        {
            "value": "morning-routine",
            "label": "Morning Routine",
            "hint": "Morning briefing",
            "kind": "skill",
            "group": "jarvis",
        }
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/morning-routine", ("morning-routine", "")),
        ("/deploy now please", ("deploy", "now please")),
        ("  /a:b.c-d\nmore", ("a:b.c-d", "more")),
        ("hello /deploy", None),
        ("/", None),
        ("", None),
        ("/ deploy", None),
    ],
)
def test_explicit_skill_reads_a_leading_slash_only(text, expected):
    assert explicit_skill(text) == expected


# ---------------------------------------------------------------- route


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = None
    app.state.agent_chat_factory = lambda: AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    return app


def test_typeahead_route_resolves_the_seat_and_reads_the_folder(
    tmp_path: Path, account: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
):
    import jarvis.ui.web.agent_chat_routes as routes

    seen: list[tuple[str, str]] = []

    def _resolve(provider: str, *, surface: str = "agent") -> str:
        seen.append((provider, surface))
        return "claude-cli"

    monkeypatch.setattr(routes, "resolve_runner", _resolve)
    _skill(folder / ".claude" / "skills", "deploy", "Ship it")

    with TestClient(_app(tmp_path)) as client:
        body = client.get(
            "/api/agent-chat/typeahead",
            params={
                "trigger": "/",
                "surface": "agent",
                "provider": "claude-api",
                "cwd": str(folder),
            },
        ).json()
        assert seen == [("claude-api", "agent")]
        assert body["trigger"] == "/"
        assert [i["value"] for i in body["items"]] == ["deploy"]

        # A folder that does not exist is a 400, not a walk of nothing.
        res = client.get(
            "/api/agent-chat/typeahead",
            params={"trigger": "@", "provider": "claude-api", "cwd": str(folder / "missing")},
        )
        assert res.status_code == 400
