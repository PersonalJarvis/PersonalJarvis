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
