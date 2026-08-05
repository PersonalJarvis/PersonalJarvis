"""The developer-message silence rule and the speak-request prompts must agree.

The Codex base instructions tell the model that developer messages are silent
configuration — with ONE exception, named by its exact opening sentence. Every
prompt whose entire purpose is to be spoken (announcements, late action
results, the direct-tool retry) must therefore open with that exact sentence,
or the categorical silence rule mutes it: a When-Then task completes, Jarvis
injects the announcement, and the user hears nothing (independent review
2026-08-05, finding S1).
"""

from __future__ import annotations

import jarvis.plugins.realtime.codex_subscription as codex_subscription_mod
import jarvis.realtime.session as session_mod


def test_base_instructions_quote_the_exact_speak_request_opener() -> None:
    base = codex_subscription_mod._THREAD_BASE_INSTRUCTIONS
    assert f"'{session_mod.SPEAK_REQUEST_OPENER}'" in base
    # The rule itself must exist and stay scoped to CONFIGURATION messages.
    assert "silent configuration" in base
    assert "configuration message's arrival" in base


def test_session_channel_carries_the_rule_AND_its_exception_together() -> None:
    """Both halves must ride the channel the voice model provably reads.

    Shipping the silence half in the per-session block while the exception
    sat only in the thread-start instructions would mute announcements and
    late action results all over again (independent review C1: the one-
    speaker directive's "wait silently" plus the inline "never acknowledge"
    headers outvoted an exception the voice may never see).
    """
    directive = session_mod._ONE_SPEAKER_DIRECTIVE
    assert "silent configuration" in directive
    assert f"'{session_mod.SPEAK_REQUEST_OPENER}'" in directive
    # And the whole thing reaches the assembled session block.
    block = session_mod._session_instructions("en")
    assert f"'{session_mod.SPEAK_REQUEST_OPENER}'" in block


def test_every_speak_request_prompt_opens_with_the_exception_sentence() -> None:
    opener = session_mod.SPEAK_REQUEST_OPENER
    prompts = (
        session_mod._external_update_prompt(
            "Task finished.", language="en", kind="announcement"
        ),
        session_mod._delegate_result_prompt(
            "The window is open.", language="en", success=True
        ),
        session_mod._delegate_result_prompt(
            "It failed.", language="en", success=False, late=True
        ),
        session_mod._direct_tool_result_retry_prompt(language="en"),
        # Unreachable on the Codex adapter today (the bridge line prefers
        # send_speech there), but the same silence-rule class the moment a
        # transport lacks that preference — the reviewer's latent-flank note.
        session_mod._delegate_bridge_prompt(
            language="en", exact_text="One moment please."
        ),
    )
    for prompt in prompts:
        assert prompt.startswith(opener)


def test_configuration_payloads_never_carry_the_speak_request_opener() -> None:
    """The exception must stay reserved for genuine delivery orders."""
    opener = session_mod.SPEAK_REQUEST_OPENER
    assert opener not in codex_subscription_mod._language_pin_text("de")
    assert opener not in codex_subscription_mod._THREAD_DEVELOPER_INSTRUCTIONS
    assert opener not in session_mod._DELEGATE_ROLE_DIRECTIVE_HANDOFF
    assert opener not in session_mod._DELEGATE_REQUIRED_DIRECTIVE
    assert opener not in session_mod._DELEGATE_PENDING_DIRECTIVE
