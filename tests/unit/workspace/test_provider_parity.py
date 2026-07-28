"""One set of coding CLIs, described by six layers — bound here so it stays one.

The registry, the account layer, the voice command schema, the spoken-alias
table, the session-resume adapters and the REST payload each answer "which
coding CLIs are there?" in their own words. Nothing forced them to agree, and
the drift is the quiet kind (BUG-008's class): every layer keeps working, one of
them just stops mentioning a provider, and the symptom is a feature that is
present everywhere except the one surface a user happens to reach it from.

It had already happened twice before these tests existed — a probe type union
listing a CLI the account layer did not, and a TypeScript union missing an auth
mode the backend had been sending for weeks.

Each test below states which two layers it binds and what a user loses when
they part company.
"""

from __future__ import annotations

from jarvis import agent_accounts
from jarvis.agentic_ide import agent_sessions, intent, names
from jarvis.workspace import agents as workspace_agents


def test_every_account_platform_is_a_registered_coding_cli() -> None:
    """Registry vs. account layer.

    A platform the registry does not know cannot be launched, so its seats would
    be switchable and unusable — and the switch would report success.
    """
    assert set(agent_accounts.platforms()) <= set(workspace_agents.coding_agent_names())


def test_every_account_platform_declares_the_variable_that_moves_it() -> None:
    """Account layer vs. its own data.

    Half a mapping is worse than none: the CLI relocates part of its identity
    and keeps reading the previous login for the rest, which is a switch that
    looks like it worked.
    """
    for platform in agent_accounts.platforms():
        spec = workspace_agents.get_agent(platform)
        assert spec is not None and spec.account is not None
        assert spec.account.env, f"{platform} claims accounts with no variable"
        assert agent_accounts.env_var(platform)
        for _var, template in spec.account.env:
            assert "{dir}" in template


def test_the_voice_schema_offers_exactly_the_registered_clis() -> None:
    """Registry vs. the command schema the router brain reads.

    A CLI missing here is unreachable BY VOICE while working everywhere else —
    the pane opens from the UI, resumes and accepts prompts, and is absent from
    the one surface meant to drive it.
    """
    from jarvis.commands.registry import _coding_agent_ids

    assert set(_coding_agent_ids()) == set(workspace_agents.coding_agent_names())


def test_every_coding_cli_can_be_named_out_loud() -> None:
    """Registry vs. the spoken-alias table.

    One unmatched word takes the whole spawn group it appeared in, so a CLI with
    no spelling does not merely fail to open — it silently deletes the panes
    requested alongside it.
    """
    for name in workspace_agents.coding_agent_names():
        entry = workspace_agents.get_agent(name)
        assert entry is not None and entry.spoken_aliases, f"{name} cannot be said"
        for spelling in entry.spoken_aliases:
            assert intent._canonical_agent(spelling) == name


def test_no_spelling_claims_two_different_clis() -> None:
    """A collision here sends work to the wrong agent, and looks like it worked."""
    seen: dict[str, str] = {}
    for name in workspace_agents.coding_agent_names():
        entry = workspace_agents.get_agent(name)
        assert entry is not None
        for spelling in (*entry.spoken_aliases, *entry.spoken_aliases_needing_suffix):
            assert spelling not in seen or seen[spelling] == name, (
                f"{spelling!r} is claimed by both {seen.get(spelling)} and {name}"
            )
            seen[spelling] = name


def test_no_pane_call_sign_can_shadow_a_cli_name() -> None:
    """Registry vs. the call-signs panes are actually handed.

    Addressing a pane called "Kimi" by saying "Kimi" is a coin flip, and the
    loser gets somebody else's instruction. Positional call-signs make that
    structurally impossible, and this pins that it stays so.
    """
    reserved = names._reserved()
    assert not {n.lower() for n in names.default_names(20)} & reserved
    for name in workspace_agents.coding_agent_names():
        assert name.lower() in reserved


def test_every_coding_cli_either_resumes_or_says_it_cannot() -> None:
    """Registry vs. the resume adapters.

    Not every CLI has to be resumable — but the answer must be the SAME one the
    pane acts on, or a workspace offers to reopen a conversation it then cannot
    find, and the pane dies on its launch instead of starting fresh.
    """
    for name in workspace_agents.coding_agent_names():
        entry = workspace_agents.get_agent(name)
        assert entry is not None
        resumable = agent_sessions.can_resume(name)
        if not resumable:
            # An entry that cannot resume must also mint no handle, or the
            # workspace would store a pointer nothing can dereference.
            assert agent_sessions.launch_extra(name) == ((), None)
            continue
        argv, handle = agent_sessions.launch_extra(name)
        if handle is not None:
            assert agent_sessions.resume_argv(name, handle) is not None
        # A launch profile shares its adapter with the entry it borrows from;
        # anything else owns one named after itself.
        assert entry.adapter_key in agent_sessions._ADAPTERS


def test_a_launch_profile_borrows_a_binary_that_is_really_registered() -> None:
    """An entry whose binary belongs to another entry must not outlive it.

    This is what keeps a provider with no CLI of its own honest: it is only
    offerable because the CLI it borrows is offerable too.
    """
    executables = {
        e.executable for e in workspace_agents.coding_agents() if not e.resume_adapter
    }
    for entry in workspace_agents.coding_agents():
        if not entry.resume_adapter:
            continue
        borrowed = workspace_agents.get_agent(entry.resume_adapter)
        assert borrowed is not None, f"{entry.name} borrows a missing entry"
        assert entry.executable == borrowed.executable
        assert entry.executable in executables


def test_every_trust_spec_names_a_format_that_has_a_writer() -> None:
    """Registry vs. the trust writers.

    A spec with no writer means the pre-seed silently does nothing and every
    pane opens on the dialog this whole mechanism exists to skip.
    """
    from jarvis.workspace.trust import _WRITERS

    for entry in workspace_agents.coding_agents():
        if entry.trust is None:
            continue
        assert entry.trust.fmt in _WRITERS
        assert entry.trust.filename


def test_the_rest_payload_lists_the_same_platforms_as_the_backend() -> None:
    """Account layer vs. what the UI is handed.

    The UI now renders whatever this payload contains, so a platform missing
    here is a section that is simply never drawn — with no error anywhere.
    """
    from jarvis.ui.web.agent_accounts_routes import _collect

    payload = _collect()
    listed = {group["platform"] for group in payload["platforms"]}
    assert listed == set(agent_accounts.platforms())
    for group in payload["platforms"]:
        # The name the UI shows comes from here, so it must never be blank.
        assert group["display_name"]
