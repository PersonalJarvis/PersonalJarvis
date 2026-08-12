"""ClaudeCliBrain — the Anthropic subscription (CLI) brain.

The invocation builder is pure on purpose: what this brain gets wrong is not
"the subprocess failed" but "the answer came back in the wrong SHAPE", and shape
is decided entirely by argv plus the stdin payload. Testing that pair directly
catches the failure that matters without spawning a CLI.
"""
from __future__ import annotations

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain.claude_cli import ClaudeCliBrain


def _req(system: str = "CONTRACT", user: str = "payload") -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content=user),),
        system=system,
        max_tokens=8000,
        stream=True,
    )


def test_structured_mode_forwards_the_system_contract_verbatim() -> None:
    """A structured caller's system prompt IS the product and must survive.

    Without this the CLI wrapper replaces the contract with "answer in one to
    three short sentences", which returns a plausible non-brief — the invisible
    degradation this whole provider exists to avoid.
    """
    brain = ClaudeCliBrain(structured_prompts=True)
    argv, prompt = brain.build_invocation(_req(system="WRITE A BRIEF", user="do X"))
    assert "WRITE A BRIEF" in " ".join(argv) or "WRITE A BRIEF" in prompt
    assert "do X" in prompt


def test_conversational_mode_does_not_leak_the_router_prompt() -> None:
    """A voice turn stays short and never carries the heavy tool prompt."""
    brain = ClaudeCliBrain(structured_prompts=False)
    _argv, prompt = brain.build_invocation(
        _req(system="ROUTER PROMPT WITH TOOLS", user="hello")
    )
    assert "ROUTER PROMPT WITH TOOLS" not in prompt
    assert "hello" in prompt


def test_invocation_disables_tools_and_pins_print_mode() -> None:
    """The brain answers; it must not be able to edit files or run commands."""
    brain = ClaudeCliBrain(structured_prompts=True)
    argv, _prompt = brain.build_invocation(_req())
    assert "-p" in argv or "--print" in argv
    joined = " ".join(argv)
    assert "--disallowed-tools" in joined or "--allowed-tools" in joined


def test_model_is_only_passed_when_configured() -> None:
    """No model id is hardcoded (AP-21) — the CLI's own default wins."""
    default_argv, _ = ClaudeCliBrain().build_invocation(_req())
    assert "--model" not in default_argv
    pinned_argv, _ = ClaudeCliBrain(model="haiku").build_invocation(_req())
    assert "--model" in pinned_argv
    assert "haiku" in pinned_argv


def test_cli_timeout_override_is_honoured() -> None:
    """A slow background caller may buy more time than the voice-tier cap."""
    assert ClaudeCliBrain(cli_timeout_s=300).cli_timeout_s == pytest.approx(300.0)
    assert ClaudeCliBrain().cli_timeout_s > 0
    # A nonsense value falls back to the default rather than disabling the cap.
    assert ClaudeCliBrain(cli_timeout_s=0).cli_timeout_s > 0
    assert ClaudeCliBrain(cli_timeout_s="nonsense").cli_timeout_s > 0  # type: ignore[arg-type]


def test_tool_turns_are_refused_not_confabulated() -> None:
    """The CLI path cannot emit tool calls; saying so lets the manager delegate."""
    assert ClaudeCliBrain().can_call_tools() is False
    assert ClaudeCliBrain().supports_vision is False


def test_subscription_probe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver calls this on every candidate; an exception there would kill
    a turn that should merely have skipped one provider."""

    def _explode() -> object:
        raise OSError("no keychain on this host")

    monkeypatch.setattr(
        "jarvis.claude_auth.ClaudeAuthService", lambda *a, **k: _explode()
    )
    assert ClaudeCliBrain.subscription_connected() is False


def test_api_key_login_does_not_masquerade_as_a_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "jarvis.claude_auth.ClaudeAuthService",
        lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(connected=True, mode="api_key")
        ),
    )
    assert ClaudeCliBrain.subscription_connected() is False


async def test_complete_raises_a_clear_error_when_the_cli_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A headless host with no CLI gets an actionable message, never a hang."""
    monkeypatch.setattr(
        "jarvis.plugins.brain.claude_cli._resolve_claude_binary", lambda: None
    )
    brain = ClaudeCliBrain(structured_prompts=True)
    with pytest.raises(RuntimeError, match="Claude CLI"):
        async for _delta in brain.complete(_req()):
            pass


_FAST_FLAGS = frozenset(
    {
        "--tools",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--effort",
    }
)


def test_structured_turns_shed_startup_weight_the_cli_supports() -> None:
    """A structured turn is pure writing: no tools, no MCP servers, no skills,
    no saved session. Every one of those is cold-start cost on a call the user
    is waiting through (27.5 s -> 16.4 s measured on the composer's payload)."""
    brain = ClaudeCliBrain(structured_prompts=True)
    argv, _prompt = brain.build_invocation(_req(), cli_flags=_FAST_FLAGS)
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in argv


def test_an_older_cli_never_sees_a_flag_it_would_die_on() -> None:
    """An unknown option makes the CLI exit with a usage error instead of an
    answer — a speed-up that bricks the writer on an older install (§3)."""
    brain = ClaudeCliBrain(structured_prompts=True)
    for flags in (frozenset(), None):
        argv, _prompt = brain.build_invocation(_req(), cli_flags=flags)
        for flag in _FAST_FLAGS:
            assert flag not in argv


def test_the_callers_reasoning_effort_is_forwarded() -> None:
    """The composer documents "medium" as its quality floor; the CLI's own
    default may think longer without writing a better brief."""
    from jarvis.core.protocols import BrainMessage, BrainRequest

    req = BrainRequest(
        messages=(BrainMessage(role="user", content="payload"),),
        system="CONTRACT",
        reasoning_effort="medium",
        stream=True,
    )
    brain = ClaudeCliBrain(structured_prompts=True)
    argv, _prompt = brain.build_invocation(req, cli_flags=_FAST_FLAGS)
    assert argv[argv.index("--effort") + 1] == "medium"
    # An unsupported CLI never sees it; "none" has no CLI level and stays home.
    bare, _prompt = brain.build_invocation(req, cli_flags=frozenset())
    assert "--effort" not in bare


def test_conversational_turns_keep_their_shape() -> None:
    """The lean set belongs to structured briefs; the voice path deliberately
    keeps its read-only lookups and is not this change's to alter."""
    brain = ClaudeCliBrain(structured_prompts=False)
    argv, _prompt = brain.build_invocation(_req(), cli_flags=_FAST_FLAGS)
    assert "--tools" not in argv
    assert "--strict-mcp-config" not in argv


def test_the_flag_probe_reads_the_clis_own_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ground truth over version guesswork: the help text is the CLI's own
    statement of what it accepts."""
    from types import SimpleNamespace

    from jarvis.plugins.brain import claude_cli

    claude_cli.reset_flag_probe_cache()
    monkeypatch.setattr(claude_cli, "_resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        claude_cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout="Options:\n  --tools <tools...>\n  --effort <level>\n"
        ),
    )
    try:
        flags = claude_cli._probe_supported_flags()  # noqa: SLF001
        assert {"--tools", "--effort"} <= flags
        assert "--strict-mcp-config" not in flags
    finally:
        claude_cli.reset_flag_probe_cache()


def test_a_failed_probe_degrades_to_the_minimal_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No probe answer means no gated flag — slower, never broken."""
    from jarvis.plugins.brain import claude_cli

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("no spawn on this host")

    claude_cli.reset_flag_probe_cache()
    monkeypatch.setattr(claude_cli, "_resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", _boom)
    try:
        assert claude_cli._probe_supported_flags() == frozenset()  # noqa: SLF001
    finally:
        claude_cli.reset_flag_probe_cache()
