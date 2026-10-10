"""The agent text of a permission refusal never reaches a person's readback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.platform.permission_service import agent_detail_for
from jarvis.platform.permissions import PermissionId
from jarvis.realtime.session import _speakable_failure_reason
from jarvis.voice.action_phrases import (
    action_phrase,
    cu_failure_readback,
    extract_speakable_reason,
    localize_failure_reason,
)

_AGENT_MARKERS = ("[permission_needed", "You must not", "must not retry", "the user")
_PERMISSIONS = (
    ("screen_recording", "needs_settings"),
    ("accessibility", "denied"),
    ("microphone", "not_determined"),
    ("input_monitoring", "needs_settings"),
)


def _agent_text(permission: str, reason: str) -> str:
    return agent_detail_for(PermissionId(permission), reason)


def _assert_clean(text: str) -> None:
    for marker in _AGENT_MARKERS:
        assert marker not in text, text


@pytest.mark.parametrize(("permission", "reason"), _PERMISSIONS)
def test_extract_speakable_reason_replaces_the_agent_text_by_one_cause(permission, reason) -> None:
    cause = extract_speakable_reason(_agent_text(permission, reason))

    assert cause is not None
    _assert_clean(cause)
    assert cause.count(".") == 1  # one short sentence, not the prohibitive paragraph


def test_a_wrapped_agent_refusal_is_replaced_too() -> None:
    wrapped = "could not select existing text: " + _agent_text("accessibility", "denied")

    cause = extract_speakable_reason(wrapped)

    assert cause is not None and "Accessibility" in cause
    _assert_clean(cause)


def test_a_harness_output_dict_is_replaced_too() -> None:
    output = {"exit_code": 1, "stderr": _agent_text("screen_recording", "needs_settings")}

    cause = extract_speakable_reason("exit 1", output)

    assert cause is not None and "Screen Recording" in cause
    _assert_clean(cause)


@pytest.mark.parametrize("lang", ["de", "en", "es", "pt"])
@pytest.mark.parametrize(("permission", "reason"), _PERMISSIONS)
def test_the_canned_floor_speaks_the_cause_in_the_turn_language(lang, permission, reason) -> None:
    cause = extract_speakable_reason(_agent_text(permission, reason))

    line = action_phrase("action_failed_reason", lang, reason=localize_failure_reason(cause, lang))

    _assert_clean(line)
    if lang != "en":
        assert "Personal Jarvis does not have" not in line  # not left in English


@pytest.mark.parametrize("lang", ["de", "en", "es", "pt"])
def test_the_computer_use_readback_never_speaks_the_agent_text(lang) -> None:
    line = cu_failure_readback(lang, error=_agent_text("screen_recording", "denied"), exit_code=1)

    _assert_clean(line)


def test_an_unknown_permission_token_still_degrades_to_a_fixed_sentence() -> None:
    cause = extract_speakable_reason("[permission_needed:something_new] This action cannot run.")

    assert cause == "A macOS permission is missing right now."


def test_a_system_dialog_refusal_has_its_own_cause() -> None:
    cause = extract_speakable_reason("[permission_needed:system_dialog] You must not answer it.")

    assert cause is not None and "system dialog" in cause
    assert "Systemdialog" in localize_failure_reason(cause, "de")


def test_the_realtime_failure_line_gets_the_cause_not_the_agent_text() -> None:
    result = {"success": False, "error": _agent_text("screen_recording", "needs_settings")}

    reason = _speakable_failure_reason(result)

    assert "Screen Recording" in reason
    _assert_clean(reason)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["de", "en", "es", "pt"])
async def test_the_brain_failure_readback_never_speaks_the_agent_text(lang) -> None:
    mgr = BrainManager.__new__(BrainManager)
    mgr._reply_language = lang
    result = SimpleNamespace(
        success=False, output=None, error=_agent_text("screen_recording", "needs_settings")
    )

    reply = await mgr._honest_failure_readback(
        result,
        user_text="take a screenshot",
        situation="The action could not be completed.",
        generic_key="action_failed_generic",
        reason_key="action_failed_reason",
        lang=lang,
    )

    assert reply.strip()
    _assert_clean(reply)
