"""Dual-notebook contracts shared by all Society model and OS adapters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.society.agent_tools import WikiNoteTool
from jarvis.society.knowledge import list_files, read_file
from jarvis.society.memory import MemoryRefused
from jarvis.society.memory_books import ensure_books, read_books
from jarvis.society.notebook import Entry, render
from jarvis.society.runtime import SocietyRuntime


@pytest.fixture
async def rt(tmp_path):
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    runtime = SocietyRuntime(tmp_path, cfg=lambda: cfg, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    await runtime.roster.create(name="Writer")
    try:
        yield runtime
    finally:
        await runtime.close()


def legacy_page():
    return "---\ntype: society\ntitle: Legacy\n---\n\n" + render(
        [
            Entry("profile", "The user prefers concise reports.", 9, 3, "user"),
            Entry("fact", "The staging environment uses PostgreSQL.", 6, 4, "tool"),
            Entry("rule", "Working rule: Reply always in English.", 10, 5, "user"),
        ]
    )


@pytest.mark.parametrize("legacy_name", ["memory.md", "Memory.md"])
async def test_legacy_migration_preserves_entries_ids_and_original_backup(rt, legacy_name):
    agent = await rt.roster.get("scout")
    root = rt.memory.root()
    folder = root / "society" / "scout"
    folder.mkdir(parents=True)
    original = legacy_page()
    (folder / legacy_name).write_text(original, encoding="utf-8")
    books = read_books(root, agent)
    assert {e.id for e in books["user"]} == {"profile", "rule"}
    assert {e.id for e in books["memory"]} == {"fact"}
    assert {p.name for p in folder.glob("*.md")} == {"USER.md", "MEMORY.md"}
    assert any(p.read_text(encoding="utf-8") == original for p in folder.glob("*.bak"))
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.md")}
    ensure_books(root, agent)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.glob("*.md")}
    assert read_file(rt, agent, "memory/memory.md")["content"] == (folder / "MEMORY.md").read_text(
        encoding="utf-8"
    )


async def test_writes_reads_and_corrections_are_agent_and_target_scoped(rt):
    scout = await rt.roster.get("scout")
    writer = await rt.roster.get("writer")
    first = await WikiNoteTool(rt, "scout").execute(
        {"kind": "memory", "target": "user", "text": "Prefers short replies."}, None
    )
    assert first.success and first.output["path"] == "society/scout/USER.md"
    second = await WikiNoteTool(rt, "scout").execute(
        {"kind": "memory", "target": "memory", "text": "The test service listens on port 8000."},
        None,
    )
    assert second.success and second.output["path"] == "society/scout/MEMORY.md"
    await rt.memory.remember(
        scout, "Prefers detailed replies.", operation="replace", old_text="Prefers short replies."
    )
    assert [e.text for e in rt.memory.entries(scout, target="user")] == [
        "Prefers detailed replies."
    ]
    assert "port 8000" in rt.memory.head(scout)
    assert "Prefers detailed replies." in rt.memory.head(scout)
    assert rt.memory.entries(writer) == []
    assert "port 8000" not in rt.memory.head(writer)
    assert [f["name"] for f in list_files(rt, scout)] == ["USER.md", "MEMORY.md"]
    with pytest.raises(MemoryRefused):
        await rt.memory.remember(scout, "Wrong target", target="../writer")


async def test_interrupted_two_file_migration_recovers_without_entry_loss(rt, monkeypatch):
    from jarvis.society import memory

    scout = await rt.roster.get("scout")
    folder = rt.memory.root() / "society" / "scout"
    folder.mkdir(parents=True)
    (folder / "memory.md").write_text(legacy_page(), encoding="utf-8")
    write = memory.atomic_write

    def interrupted(path, text):
        if path.name == "USER.md":
            raise OSError("Simulated process interruption")
        write(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(memory, "atomic_write", interrupted)
        with pytest.raises(OSError):
            ensure_books(rt.memory.root(), scout)
    assert (folder / ".memory-layout.pending.json").is_file()
    recovered = read_books(rt.memory.root(), scout)
    assert {e.id for entries in recovered.values() for e in entries} == {"profile", "fact", "rule"}
    assert not (folder / ".memory-layout.pending.json").exists()


async def test_existing_user_notebook_is_preserved_when_legacy_memory_is_split(rt):
    scout = await rt.roster.get("scout")
    folder = rt.memory.root() / "society" / "scout"
    folder.mkdir(parents=True)
    (folder / "memory.md").write_text(legacy_page(), encoding="utf-8")
    (folder / "USER.md").write_text(
        render([Entry("existing", "Prefers afternoon appointments.")]), encoding="utf-8"
    )
    books = read_books(rt.memory.root(), scout)
    assert {e.id for e in books["user"]} == {"existing", "profile", "rule"}


async def test_both_prompt_sections_are_bounded_without_truncating_stored_facts(rt):
    scout = await rt.roster.get("scout")
    await rt.memory.remember(scout, "Long profile detail. " * 1000, target="user", importance=1)
    await rt.memory.remember(scout, "Long project detail. " * 1000, target="memory", importance=1)
    await rt.memory.remember(scout, "Reply always in English.", target="user", importance=10)
    prompt = rt.memory.head(scout)
    assert "Your user profile" in prompt and "Your memory" in prompt
    assert "Reply always in English." in prompt
    assert len(prompt) < 14_000
    assert len(rt.memory.entries(scout, target="user")[0].text) > 4_000
    assert len(rt.memory.entries(scout, target="memory")[0].text) > 8_000


async def test_bad_legacy_metadata_never_overwrites_originals(rt):
    scout = await rt.roster.get("scout")
    folder = rt.memory.root() / "society" / "scout"
    folder.mkdir(parents=True)
    original = "<!-- memory-entry: NOT JSON -->\nImportant original text."
    path = folder / "memory.md"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError):
        ensure_books(rt.memory.root(), scout)
    assert path.read_text(encoding="utf-8") == original
    assert not (folder / "USER.md").exists()


async def test_interrupted_migration_refuses_to_overwrite_a_new_manual_edit(rt, monkeypatch):
    from jarvis.society import memory

    scout = await rt.roster.get("scout")
    folder = rt.memory.root() / "society" / "scout"
    folder.mkdir(parents=True)
    (folder / "memory.md").write_text(legacy_page(), encoding="utf-8")
    write = memory.atomic_write

    def interrupted(path, text):
        if path.name == "USER.md":
            raise OSError("Simulated interruption")
        write(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(memory, "atomic_write", interrupted)
        with pytest.raises(OSError):
            ensure_books(rt.memory.root(), scout)
    (folder / "MEMORY.md").write_text("New manual information", encoding="utf-8")
    with pytest.raises(ValueError, match="changed during migration"):
        ensure_books(rt.memory.root(), scout)
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == "New manual information"
    assert any(p.read_text(encoding="utf-8") == legacy_page() for p in folder.glob("*.bak"))


async def test_notebook_and_note_links_cannot_expose_another_agents_profile(rt):
    scout = await rt.roster.get("scout")
    writer = await rt.roster.get("writer")
    await rt.memory.remember(writer, "Private writer preference.", target="user")
    scout_books, writer_books = rt.memory.books(scout), rt.memory.books(writer)
    linked_note = scout_books["memory"].parent / "linked-note.md"
    try:
        linked_note.symlink_to(writer_books["user"])
    except OSError:
        pytest.skip("This host cannot create symlinks")
    assert not await rt.memory.recall(scout, "Private writer preference")
    scout_books["user"].unlink()
    scout_books["user"].symlink_to(writer_books["user"])
    with pytest.raises(ValueError, match="Linked notebook"):
        rt.memory.head(scout)


@pytest.mark.parametrize(
    "briefing", ["## Your user profile\nSCOUT PROFILE\n## Your memory\nSCOUT FACTS", ""]
)
def test_api_agent_prompt_does_not_read_global_profile_or_ambient_context(briefing):
    from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
    from jarvis.brain.turn_override import TurnOverride

    class PrivateGlobalData:
        def render_for_prompt(self, **kwargs):
            raise AssertionError("Global profile must not be read by an agent")

    manager = BrainManager.__new__(BrainManager)
    manager._user_profile = manager._soul = manager._people = PrivateGlobalData()
    manager._config = SimpleNamespace(performance=SimpleNamespace(cache_optimized_prompt=True))
    manager._render_live_tool_block = lambda: "SCOPED TOOL CONTRACT"
    manager._reply_language_directive = lambda: "REPLY LANGUAGE"
    manager._wiki_context_suffix = "GLOBAL PRIVATE WIKI"
    token = _TURN_OVERRIDE.set(
        TurnOverride(
            provider="test", system_extra=briefing, tool_context={"tool_origin": "society"}
        )
    )
    try:
        prompt = manager._build_system_prompt()
        assert "SCOPED TOOL CONTRACT" in prompt and "REPLY LANGUAGE" in prompt
        assert "GLOBAL PRIVATE WIKI" not in manager._build_turn_context()
        assert ("SCOUT PROFILE" in prompt) if briefing else ("unavailable" in prompt)
    finally:
        _TURN_OVERRIDE.reset(token)


async def test_global_profile_writers_are_withheld_but_private_notebook_tool_remains(rt):
    from jarvis.society.surface import society_tool_filter

    scout = await rt.roster.get("scout")
    rt.cache_agent(scout)
    names = ["remember", "update_profile", "profile-update", "update-profile", "society_wiki_note"]
    tools = {
        name: SimpleNamespace(name=name, schema={}, description=name, risk_tier="monitor")
        for name in names
    }
    filtered = society_tool_filter(SimpleNamespace(session_id=scout.session_id))(tools)
    assert set(filtered) == {"society_wiki_note"}


def test_resumed_cli_context_contains_its_own_user_and_memory_notebooks():
    from jarvis.agent_chat.jarvis_harness import society_memory_refresh

    context = society_memory_refresh(
        "## Your user profile\nUSER.md: Prefers short replies.\n\n"
        "## Your memory\nMEMORY.md: The project uses PostgreSQL.\n\n"
        "## Teammates\nOther agent profile must not ride along."
    )
    assert "USER.md: Prefers short replies." in context
    assert "MEMORY.md: The project uses PostgreSQL." in context
    assert "Other agent profile" not in context


async def test_agent_response_does_not_feed_the_global_profile_curator():
    import asyncio

    from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
    from jarvis.brain.turn_override import TurnOverride

    calls = []

    class Curator:
        async def process_turn(self, *args):
            calls.append(args)

    async def publish(**kwargs):
        return None

    manager = BrainManager.__new__(BrainManager)
    manager._curator = Curator()
    manager._publish_response_generated = publish
    token = _TURN_OVERRIDE.set(
        TurnOverride(provider="test", tool_context={"tool_origin": "society"})
    )
    try:
        await manager._record_response_side_effects(
            user_text="Private agent preference", response_text="Recorded", use_history=False
        )
        await asyncio.sleep(0)
        assert calls == []
    finally:
        _TURN_OVERRIDE.reset(token)
    await manager._record_response_side_effects(
        user_text="Global preference", response_text="Recorded", use_history=False
    )
    await asyncio.sleep(0)
    assert calls == [("Global preference", "Recorded")]
