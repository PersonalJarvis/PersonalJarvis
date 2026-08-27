"""What a pane may be opened on, and what never reaches its command line.

Three questions this file settles, because getting any of them wrong is a
silent one: does a pick actually become an argument, does a pick this CLI
cannot express get dropped instead of passed on, and does every choice a
picker offers carry a sentence a person can read?
"""

from __future__ import annotations

import pytest

from jarvis.workspace import launch_picks
from jarvis.workspace.agents import coding_agent_names, get_agent

#: Every entry that declares picks — the whole table, so an entry added later
#: is held to the same rules without a line changing here.
DECLARING = [name for name in coding_agent_names() if launch_picks.picks_for(name) is not None]


def test_some_entries_declare_picks() -> None:
    """A guard on the guard: an empty table would make every test below pass."""
    assert "claude" in DECLARING
    assert "codex" in DECLARING


@pytest.mark.parametrize("agent", DECLARING)
def test_every_offered_mode_has_a_sentence(agent: str) -> None:
    """A permission choice nobody can explain has no business in a picker."""
    for mode in launch_picks.permission_modes(agent):
        assert mode["label"] and mode["label"] != mode["id"], (
            f"{agent}: permission mode {mode['id']!r} has no label — "
            "either describe it in jarvis/agent_chat/permissions.py or stop offering it"
        )
        assert mode["description"], f"{agent}: permission mode {mode['id']!r} has no description"


@pytest.mark.parametrize("agent", DECLARING)
def test_every_offered_mode_reaches_the_command_line(agent: str) -> None:
    """Offering a stance the launch cannot express would be a dead control."""
    for mode in launch_picks.permission_modes(agent):
        assert launch_picks.launch_argv(agent, permission_mode=mode["id"])


@pytest.mark.parametrize("agent", DECLARING)
def test_every_offered_model_reaches_the_command_line(agent: str) -> None:
    for model in launch_picks.offered_models(agent):
        argv = launch_picks.launch_argv(agent, model=model["id"])
        assert model["id"] in argv


def test_claude_picks_become_its_own_flags() -> None:
    assert launch_picks.launch_argv(
        "claude", model="claude-opus-5", effort="high", permission_mode="acceptEdits"
    ) == ("--model", "claude-opus-5", "--effort", "high", "--permission-mode", "acceptEdits")


def test_codex_effort_is_a_config_override_not_a_flag() -> None:
    """Codex has no ``--effort``; the level rides in as a ``-c`` override.

    Bare, not quoted: the value is parsed as TOML and falls back to the
    literal string, so quotes here would end up inside the setting.
    """
    argv = launch_picks.launch_argv("codex", effort="xhigh")
    assert argv == ("-c", "model_reasoning_effort=xhigh")


def test_codex_permission_words_are_sandbox_flags() -> None:
    assert launch_picks.launch_argv("codex", permission_mode="auto") == (
        "--sandbox",
        "workspace-write",
    )
    assert launch_picks.launch_argv("codex", permission_mode="full-access") == (
        "--dangerously-bypass-approvals-and-sandbox",
    )


def test_a_model_the_cli_does_not_offer_is_dropped() -> None:
    """A stale pick costs the CLI's own default, never a pane that will not start."""
    assert launch_picks.launch_argv("codex", model="claude-opus-5") == ()
    assert launch_picks.normalize_model("codex", "claude-opus-5") == ""


def test_a_flag_cannot_be_smuggled_in_as_a_model() -> None:
    """These strings come from a browser; an argv reads a stray flag as an order."""
    for hostile in ("--dangerously-skip-permissions", "-c evil=1", "a b", "$(whoami)", "x;y"):
        assert launch_picks.normalize_model("opencode", hostile) == "", hostile
        assert launch_picks.launch_argv("opencode", model=hostile) == (), hostile


def test_a_cli_with_no_published_list_takes_the_users_own_id() -> None:
    """OpenCode's models are the person's accounts, so the SHAPE is the check."""
    assert launch_picks.launch_argv("opencode", model="anthropic/claude-opus-5") == (
        "--model",
        "anthropic/claude-opus-5",
    )


def test_an_unknown_permission_mode_is_dropped_never_folded() -> None:
    """Landing near a stance is worse than landing on the CLI's ask-first default."""
    assert launch_picks.normalize_permission("claude", "full-access") == ""
    assert launch_picks.launch_argv("claude", permission_mode="full-access") == ()


def test_an_effort_off_the_ladder_snaps_down_never_up() -> None:
    """A level this CLI has no word for must not become a stronger one."""
    assert launch_picks.normalize_effort("antigravity", "max") == "high"
    assert launch_picks.normalize_effort("claude", "ultra") == "max"


def test_a_cli_that_needs_an_effort_with_its_model_gets_one() -> None:
    """agy rejects a base Gemini id on its own — half an instruction kills the pane."""
    picks = launch_picks.picks_for("antigravity")
    assert picks is not None and picks.effort_required
    model = launch_picks.offered_models("antigravity")[0]["id"]
    argv = launch_picks.launch_argv("antigravity", model=model)
    assert "--effort" in argv


def test_an_entry_that_declares_nothing_offers_nothing() -> None:
    """A plain shell has no model to run on, and says so rather than pretending."""
    assert get_agent("shell") is not None
    assert launch_picks.launch_argv("shell", model="anything", permission_mode="auto") == ()
    assert launch_picks.offered("shell")["permission_modes"] == []


def test_an_unknown_entry_is_answered_not_raised() -> None:
    assert launch_picks.launch_argv("no-such-cli", model="x") == ()
    assert launch_picks.offered("no-such-cli")["models"] == []


def test_the_pane_retells_where_an_approval_is_answered() -> None:
    """The chat's ladder points at a card in the chat; a pane asks in its TUI.

    Pinned because the sentence is a substitution rather than a second ladder:
    reword the chat's copy and this fails instead of quietly leaving a picker
    telling people to look somewhere that has nothing on it.
    """
    from jarvis.agent_chat.permissions import permission_modes as chat_ladder

    chat = {m.id: m.description for m in chat_ladder("claude-cli")}
    assert "an approval card here in the chat" in chat["default"], (
        "the chat ladder no longer says this — update _CHAT_PLACE in launch_picks.py"
    )
    pane = {m["id"]: m["description"] for m in launch_picks.permission_modes("claude")}
    assert "here in the chat" not in pane["default"]
    assert "terminal" in pane["default"]


def test_offered_answers_in_the_composers_own_shape() -> None:
    """The IDE's picker is the chat's composer; it reads these exact keys."""
    offered = launch_picks.offered("claude")
    assert set(offered) == {
        "models",
        "default_model",
        "effort_levels",
        "default_effort",
        "permission_modes",
        "default_permission_mode",
    }
    assert offered["default_effort"] in offered["effort_levels"]
    # A pane opens in the CLI's own stance until somebody picks another.
    assert offered["default_permission_mode"] == ""


def test_a_live_list_replaces_the_curated_one() -> None:
    """What THIS account can pick beats what this app shipped a release ago."""
    live = {"codex-cli": [{"id": "gpt-9-imaginary", "label": "GPT-9"}]}
    assert launch_picks.offered_models("codex", live) == live["codex-cli"]
    # And a pick off that list is accepted, not measured against the fallback.
    assert launch_picks.runner_of("codex") == "codex-cli"
