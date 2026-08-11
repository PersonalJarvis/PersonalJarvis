"""Terminal CLIs the user adds: storing them, and what the registry makes of them.

Every test runs against a temporary app-data directory, so nothing here can see
or damage the maintainer's own stored CLIs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.workspace import agents as registry
from jarvis.workspace import custom_clis


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the store at a scratch directory and forget it again afterwards.

    The registry caches the store by revision number, so the cache has to be
    dropped on both sides of the test: on the way in, because a previous test's
    entries would still be registered, and on the way out, because these ones
    must not leak into the next.
    """
    root = tmp_path / "workspace-clis"
    monkeypatch.setattr(custom_clis, "workspace_clis_dir", lambda: root)
    monkeypatch.setattr(custom_clis, "workspace_clis_path", lambda: root / "custom.json")
    registry.refresh_custom_agents()
    yield root
    monkeypatch.undo()
    registry.refresh_custom_agents()


# --------------------------------------------------------------------------
# Storing
# --------------------------------------------------------------------------


def test_a_stored_cli_is_offered_like_a_built_in() -> None:
    """The whole point: one form, and every surface offers it."""
    entry = custom_clis.create_custom_cli(
        "Antigravity", "agy", description="Google's terminal coding CLI."
    )
    registry.refresh_custom_agents()

    assert entry.id == "antigravity"
    assert entry.id in registry.coding_agent_names()
    assert entry.id in registry.agent_names()

    agent = registry.get_agent(entry.id)
    assert agent is not None
    assert agent.display_name == "Antigravity"
    assert agent.is_coding_agent
    assert agent.custom is True
    assert agent.executable == "agy"
    # No trust file of ours to seed and no install command we could honestly
    # offer — both must degrade to "nothing", never to a guess.
    assert agent.needs_trust is False
    assert registry.install_command(entry.id) is None


def test_the_plain_terminal_stays_last_in_the_menu() -> None:
    """A "no agent at all" choice buried among CLIs reads as one of them."""
    custom_clis.create_custom_cli("Antigravity", "agy")
    registry.refresh_custom_agents()
    assert registry.agent_names()[-1] == registry.PLAIN_TERMINAL


def test_a_rename_keeps_the_id() -> None:
    """Panes, saved workspaces and resume offers all recorded the old id."""
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    renamed = custom_clis.update_custom_cli(entry.id, display_name="Antigravity CLI")
    assert renamed.id == entry.id
    assert renamed.display_name == "Antigravity CLI"


def test_a_name_that_is_taken_gets_its_own_id() -> None:
    """Two entries answering to one name is a pane running the wrong tool."""
    first = custom_clis.create_custom_cli("Antigravity", "agy")
    second = custom_clis.create_custom_cli("Antigravity", "agy2")
    assert first.id != second.id
    assert second.id.startswith("antigravity")


def test_a_built_in_name_cannot_be_claimed() -> None:
    entry = custom_clis.create_custom_cli("Codex", "not-really-codex")
    assert entry.id != "codex"
    assert registry.get_agent("codex") is not None
    assert registry.get_agent("codex").display_name == "Codex"


def test_accents_survive_into_the_id() -> None:
    """An id built only from the ASCII that survived is unrecognisable."""
    entry = custom_clis.create_custom_cli("Café CLI", "cafe")
    assert entry.id == "cafe-cli"


@pytest.mark.parametrize(
    ("name", "command"),
    [("", "agy"), ("  ", "agy"), ("Antigravity", ""), ("Antigravity", "  ")],
)
def test_a_blank_name_or_command_is_refused(name: str, command: str) -> None:
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.create_custom_cli(name, command)


def test_a_multi_line_command_is_refused() -> None:
    """A command line, not a script — see MAX_COMMAND_LEN's neighbours."""
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.create_custom_cli("Two Things", "agy\nrm -rf /")


def test_deleting_removes_it_from_the_registry() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    registry.refresh_custom_agents()
    assert entry.id in registry.coding_agent_names()

    custom_clis.delete_custom_cli(entry.id)
    registry.refresh_custom_agents()
    assert entry.id not in registry.coding_agent_names()
    assert registry.get_agent(entry.id) is None


def test_a_corrupt_store_does_not_hide_the_built_ins(isolated_store: Path) -> None:
    """One hand-edited file must not take the shipped CLIs down with it."""
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "custom.json").write_text("{ not json", encoding="utf-8")
    registry.refresh_custom_agents()
    assert "claude" in registry.coding_agent_names()
    assert custom_clis.list_custom_clis() == []


def test_an_incomplete_entry_is_skipped_not_fatal(isolated_store: Path) -> None:
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "custom.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "broken"},  # no name, no command
                    {"id": "good", "display_name": "Good", "command": "good"},
                ]
            }
        ),
        encoding="utf-8",
    )
    stored = custom_clis.list_custom_clis()
    assert [entry.id for entry in stored] == ["good"]


def test_a_hand_written_entry_cannot_shadow_a_built_in(isolated_store: Path) -> None:
    """The store refuses to CREATE one; this is the file being edited by hand."""
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "custom.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "codex", "display_name": "Impostor", "command": "impostor"}
                ]
            }
        ),
        encoding="utf-8",
    )
    registry.refresh_custom_agents()
    shipped = registry.get_agent("codex")
    assert shipped is not None
    assert shipped.display_name == "Codex"
    assert shipped.custom is False


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def test_a_windows_path_survives_the_split() -> None:
    """POSIX shlex would eat the backslashes and report a working CLI missing."""
    parts = custom_clis.split_command(r'C:\tools\agy.exe --flag "two words"')
    assert parts == (r"C:\tools\agy.exe", "--flag", "two words")


@pytest.mark.parametrize(
    ("command", "through_shell"),
    [
        ("agy", False),
        ("npx -y some-cli", False),
        ("agy | tee log.txt", True),
        ("agy && echo done", True),
        ("FOO=1 agy", True),
        ("agy --model $MODEL", True),
    ],
)
def test_shell_source_is_told_apart_from_an_argv(
    command: str, through_shell: bool
) -> None:
    assert custom_clis.needs_shell(command) is through_shell


def test_an_environment_prefix_does_not_become_the_binary() -> None:
    """`FOO=1 agy` must look for `agy`, not for a program called `FOO=1`."""
    entry = custom_clis.create_custom_cli("Prefixed", "FOO=1 agy --flag")
    assert entry.binary == "agy"


def test_a_shell_entry_carries_no_half_parsed_argv() -> None:
    custom_clis.create_custom_cli("Piped", "agy | tee log.txt")
    registry.refresh_custom_agents()
    agent = registry.get_agent("piped")
    assert agent is not None
    assert agent.shell_launch is True
    assert agent.launch_args == ()


def test_a_plain_entry_keeps_its_arguments() -> None:
    custom_clis.create_custom_cli("Npx Tool", "npx -y some-cli")
    registry.refresh_custom_agents()
    agent = registry.get_agent("npx-tool")
    assert agent is not None
    assert agent.shell_launch is False
    assert agent.executable == "npx"
    assert agent.launch_args == ("-y", "some-cli")


def test_a_shell_launch_argv_exits_with_the_agent() -> None:
    """A surviving prompt reads as a live agent to every readiness check."""
    argv = registry.shell_run_argv("agy | tee log.txt")
    if argv is None:  # a host with no shell at all
        pytest.skip("no shell on this host")
    assert "-NoExit" not in argv
    assert "/k" not in argv


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detection_never_runs_a_strangers_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep shares the event loop the wake microphone is delivered on."""
    custom_clis.create_custom_cli("Antigravity", "agy")
    registry.refresh_custom_agents()

    probed: list[str] = []

    class RecordingProber:
        async def probe_all(self, specs):  # noqa: ANN001, ANN202
            from jarvis.clis.spec import CliStatus

            probed.extend(spec.name for spec in specs)
            return {spec.name: CliStatus(installed=False, version=None) for spec in specs}

    monkeypatch.setattr(registry, "_on_path", lambda binary: binary == "agy")
    infos = {i.name: i for i in await registry.detect_agents(RecordingProber())}

    assert "antigravity" not in probed
    assert infos["antigravity"].installed is True
    # No version, ever: asking would be the subprocess this path avoids.
    assert infos["antigravity"].version is None
    assert infos["antigravity"].install_command is None
    assert infos["antigravity"].custom is True


@pytest.mark.asyncio
async def test_a_missing_binary_is_reported_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_clis.create_custom_cli("Nowhere", "definitely-not-installed-xyz")
    registry.refresh_custom_agents()

    class QuietProber:
        async def probe_all(self, specs):  # noqa: ANN001, ANN202
            from jarvis.clis.spec import CliStatus

            return {spec.name: CliStatus(installed=False, version=None) for spec in specs}

    monkeypatch.setattr(registry, "_on_path", lambda binary: False)
    infos = {i.name: i for i in await registry.detect_agents(QuietProber())}
    assert infos["nowhere"].installed is False


# --------------------------------------------------------------------------
# Logos
# --------------------------------------------------------------------------


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_a_logo_is_stored_and_served() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    updated = custom_clis.set_logo(entry.id, SVG, "mark.svg")
    assert updated.logo == "antigravity.svg"
    assert custom_clis.logo_url(updated) == "/api/workspace-clis/antigravity/logo"

    found = custom_clis.logo_file(entry.id)
    assert found is not None
    path, media = found
    assert media == "image/svg+xml"
    assert path.read_bytes() == SVG


def test_replacing_a_logo_leaves_no_stale_sibling() -> None:
    """A later lookup keyed on the id would otherwise pick up the old file."""
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    custom_clis.set_logo(entry.id, SVG, "mark.svg")
    updated = custom_clis.set_logo(entry.id, PNG, "mark.png")
    assert updated.logo == "antigravity.png"
    assert not (custom_clis.logo_dir() / "antigravity.svg").exists()


def test_a_file_that_is_not_what_it_claims_is_refused() -> None:
    """These bytes get handed back out over HTTP."""
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.set_logo(entry.id, b"PK\x03\x04 a zip file", "mark.svg")
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.set_logo(entry.id, b"not a png at all", "mark.png")


def test_an_unknown_extension_is_refused() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.set_logo(entry.id, b"MZ binary", "mark.exe")


def test_an_oversized_logo_is_refused() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    huge = SVG + b" " * custom_clis.MAX_LOGO_BYTES
    with pytest.raises(custom_clis.CustomCliError):
        custom_clis.set_logo(entry.id, huge, "mark.svg")


def test_a_hand_edited_logo_path_cannot_escape_the_logo_directory(
    isolated_store: Path,
) -> None:
    """The file name is rebuilt from the id rather than trusted from the store."""
    isolated_store.mkdir(parents=True, exist_ok=True)
    (isolated_store / "custom.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "evil",
                        "display_name": "Evil",
                        "command": "evil",
                        "logo": "../../../../.env",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert custom_clis.logo_file("evil") is None


def test_deleting_an_entry_takes_its_logo_with_it() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    custom_clis.set_logo(entry.id, SVG, "mark.svg")
    stored = custom_clis.logo_dir() / "antigravity.svg"
    assert stored.exists()

    custom_clis.delete_custom_cli(entry.id)
    assert not stored.exists()


def test_clearing_a_logo_falls_back_to_the_monogram() -> None:
    entry = custom_clis.create_custom_cli("Antigravity", "agy")
    custom_clis.set_logo(entry.id, SVG, "mark.svg")
    cleared = custom_clis.clear_logo(entry.id)
    assert cleared.logo == ""
    assert custom_clis.logo_url(cleared) == ""
    assert custom_clis.logo_file(entry.id) is None


# --------------------------------------------------------------------------
# Speech
# --------------------------------------------------------------------------


def test_a_short_one_word_name_claims_no_spoken_alias() -> None:
    """An alias like "go" turns every sentence containing it into a spawn."""
    custom_clis.create_custom_cli("Go", "go-cli")
    registry.refresh_custom_agents()
    assert "go" not in registry.spoken_aliases()


def test_a_real_name_is_addressable_by_voice() -> None:
    custom_clis.create_custom_cli("Antigravity", "agy")
    registry.refresh_custom_agents()
    assert registry.spoken_aliases().get("antigravity") == "antigravity"


def test_a_custom_name_is_reserved_against_pane_call_signs() -> None:
    """Saying "Antigravity" must not be a coin flip between a pane and a CLI."""
    custom_clis.create_custom_cli("Antigravity", "agy")
    registry.refresh_custom_agents()
    assert "antigravity" in registry.reserved_call_signs()
