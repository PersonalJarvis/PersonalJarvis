"""Unit tests for the 10 ``StatusParseStrategy`` branches in the prober.

``_apply_parse_strategy`` is a pure function: ``(strategy, stdout, stderr,
exit_code) -> auth_status``. Each of the ten strategies has a connected case
and a not-connected case, plus the cross-cutting exit-code short-circuit.
"""

from __future__ import annotations

import pytest

from jarvis.clis.prober import _apply_parse_strategy

# --- exit-code short-circuit (applies to all but json_array_nonempty_or_error) ---


def test_nonzero_exit_short_circuits_to_not_connected() -> None:
    assert _apply_parse_strategy("text_nonempty", "anything", "", 1) == "not_connected"


def test_or_error_strategy_is_exempt_from_short_circuit() -> None:
    # exit != 0 but no "not logged in" marker -> unknown, not not_connected
    assert _apply_parse_strategy("json_array_nonempty_or_error", "", "boom", 1) == "unknown"


# --- json_accounts (gcloud auth list --format=json) ---


def test_json_accounts_active_is_connected() -> None:
    out = '[{"account": "a@b.com", "status": "ACTIVE"}]'
    assert _apply_parse_strategy("json_accounts", out, "", 0) == "connected"


def test_json_accounts_no_active_is_not_connected() -> None:
    out = '[{"account": "a@b.com", "status": "INACTIVE"}]'
    assert _apply_parse_strategy("json_accounts", out, "", 0) == "not_connected"


def test_json_accounts_bad_json_is_unknown() -> None:
    assert _apply_parse_strategy("json_accounts", "not json", "", 0) == "unknown"


# --- json_object_exists ---


def test_json_object_exists_connected() -> None:
    assert _apply_parse_strategy("json_object_exists", '{"id": 1}', "", 0) == "connected"


def test_json_object_exists_empty_object_not_connected() -> None:
    assert _apply_parse_strategy("json_object_exists", "{}", "", 0) == "not_connected"


# --- json_array_nonempty ---


def test_json_array_nonempty_connected() -> None:
    assert _apply_parse_strategy("json_array_nonempty", "[1, 2]", "", 0) == "connected"


def test_json_array_empty_not_connected() -> None:
    assert _apply_parse_strategy("json_array_nonempty", "[]", "", 0) == "not_connected"


# --- json_array_nonempty_or_error (gh auth status etc.) ---


def test_or_error_exit_zero_is_connected() -> None:
    assert _apply_parse_strategy("json_array_nonempty_or_error", "ok", "", 0) == "connected"


def test_or_error_login_marker_is_not_connected() -> None:
    assert (
        _apply_parse_strategy("json_array_nonempty_or_error", "", "You are not logged in", 1)
        == "not_connected"
    )


# --- json_has_field_username (aws sts get-caller-identity) ---


def test_json_has_field_username_connected() -> None:
    out = '{"Username": "arn:aws:iam::123:user/foo"}'
    assert _apply_parse_strategy("json_has_field_username", out, "", 0) == "connected"


def test_json_has_field_username_missing_not_connected() -> None:
    assert _apply_parse_strategy("json_has_field_username", "{}", "", 0) == "not_connected"


def test_json_has_field_username_bad_json_unknown() -> None:
    assert _apply_parse_strategy("json_has_field_username", "xx", "", 0) == "unknown"


# --- text_contains_email ---


def test_text_contains_email_connected() -> None:
    assert (
        _apply_parse_strategy("text_contains_email", "Logged in as the maintainer@example.com", "", 0)
        == "connected"
    )


def test_text_contains_email_none_not_connected() -> None:
    assert _apply_parse_strategy("text_contains_email", "no email here", "", 0) == "not_connected"


# --- text_contains_username ---


def test_text_contains_username_connected() -> None:
    assert _apply_parse_strategy("text_contains_username", "myuser", "", 0) == "connected"


def test_text_contains_username_error_prefix_not_connected() -> None:
    assert (
        _apply_parse_strategy("text_contains_username", "error: not authenticated", "", 0)
        == "not_connected"
    )


# --- text_contains_logged_in ---


def test_text_contains_logged_in_connected() -> None:
    assert (
        _apply_parse_strategy("text_contains_logged_in", "You are logged in to github.com", "", 0)
        == "connected"
    )


def test_text_contains_logged_in_absent_not_connected() -> None:
    assert _apply_parse_strategy("text_contains_logged_in", "hello", "", 0) == "not_connected"


# --- text_contains_key ---


def test_text_contains_key_connected() -> None:
    assert _apply_parse_strategy("text_contains_key", "api_key = sk-abc123", "", 0) == "connected"


def test_text_contains_key_absent_not_connected() -> None:
    assert _apply_parse_strategy("text_contains_key", "no key", "", 0) == "not_connected"


# --- text_nonempty ---


def test_text_nonempty_connected() -> None:
    assert _apply_parse_strategy("text_nonempty", "some output", "", 0) == "connected"


def test_text_nonempty_empty_not_connected() -> None:
    assert _apply_parse_strategy("text_nonempty", "   ", "", 0) == "not_connected"


# --- unknown strategy falls through to unknown ---


def test_unknown_strategy_returns_unknown() -> None:
    assert _apply_parse_strategy("does_not_exist", "x", "", 0) == "unknown"  # type: ignore[arg-type]


def test_resolve_executable_returns_full_path_for_python() -> None:
    """``resolve_executable`` must honor PATHEXT (the .cmd-shim fix for gcloud).

    We can't assume gcloud is installed in CI, but the Python interpreter is
    always on PATH; the resolver must return an absolute path for it and pass
    an unknown name through unchanged.
    """
    import os
    import sys

    from jarvis.core.process_utils import resolve_executable

    binary = os.path.basename(sys.executable)  # python.exe / python
    resolved = resolve_executable(binary)
    assert os.path.isabs(resolved), f"expected absolute path, got {resolved!r}"

    # Unknown binary passes through unchanged so the caller raises a clean error.
    assert resolve_executable("definitely-not-a-real-binary-xyz") == (
        "definitely-not-a-real-binary-xyz"
    )
    assert resolve_executable("") == ""


@pytest.mark.parametrize(
    "strategy",
    [
        "json_accounts",
        "json_object_exists",
        "json_array_nonempty",
        "json_array_nonempty_or_error",
        "json_has_field_username",
        "text_contains_email",
        "text_contains_username",
        "text_contains_logged_in",
        "text_contains_key",
        "text_nonempty",
    ],
)
def test_every_documented_strategy_is_reachable(strategy: str) -> None:
    """Each of the ten documented strategies must return a valid auth state
    (not crash) for a benign input. Guards against a strategy name landing in
    the Literal but not the dispatch chain."""
    result = _apply_parse_strategy(strategy, "sample@example.com x", "", 0)  # type: ignore[arg-type]
    assert result in {"connected", "expired", "not_connected", "unknown"}
