"""Tests for jarvis.awareness.privacy.PrivacyFilter.

Plan §4 smoke expectations + hybrid default behavior + user-patterns additivity.

PrivacyFilter mirrors jarvis.safety.risk_tier: SYSTEM patterns are hard,
USER can only block additively (never remove system defaults). Order:
BLOCK > ALLOW > default hybrid (block-for-browsers).
"""
from __future__ import annotations

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.privacy import PrivacyFilter


def _filter() -> PrivacyFilter:
    return PrivacyFilter(AwarenessConfig.default())


# --- Plan §4 smoke test (binding) ------------------------------------------

def test_blocks_banking_title() -> None:
    """Plan smoke 1: Sparkasse-Online-Banking → BLOCKED, reason matches."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Sparkasse Online-Banking - Mozilla Firefox",  # i18n-allow
        process_name="firefox.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_title:")
    # A banking pattern must match — either *Banking* or *Sparkasse* or *Online-Banking*
    assert any(p in reason for p in ("*Banking*", "*Sparkasse*", "*Online-Banking*"))  # i18n-allow


def test_allows_vscode() -> None:
    """Plan smoke 2: VS Code → ALLOWED, reason matches."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="pipeline.py - jarvis - Visual Studio Code",
        process_name="code.exe",
    )
    assert allowed is True
    assert reason == "matched_allowed_process:code.exe"


# --- Hybrid default (D-A1) --------------------------------------------------

def test_browser_without_explicit_block_still_blocked_via_hybrid() -> None:
    """D-A1: a browser without a title match → BLOCKED (default block_for_browsers)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Wikipedia — The Free Encyclopedia",
        process_name="firefox.exe",
    )
    assert allowed is False
    assert reason == "default_block_for_browser"


def test_unknown_non_browser_allowed_via_hybrid() -> None:
    """D-A1: Notepad or similar → ALLOWED (default allow_for_others)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Untitled - Notepad",
        process_name="notepad.exe",
    )
    assert allowed is True
    assert reason == "default_allow_for_unknown"


def test_chrome_blocked_by_hybrid() -> None:
    """Chrome counts as a browser → the hybrid default blocks it."""
    pf = _filter()
    allowed, _ = pf.is_allowed(
        window_title="GitHub - Mozilla Firefox",
        process_name="chrome.exe",
    )
    assert allowed is False


# --- BLOCK trumps ALLOW + order ---------------------------------------------

def test_blocked_process_trumps_allowed_process() -> None:
    """1password title in code.exe → BLOCK wins (the process blacklist applies)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="My Vault - 1Password",
        process_name="1password.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_process:1password*")


def test_blocked_title_trumps_allowed_process() -> None:
    """Banking title in code.exe (e.g. a PDF view) → the title blacklist wins."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Sparkasse Statement.pdf - Code",  # i18n-allow
        process_name="code.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_title:")


# --- User patterns are additive ----------------------------------------------

def test_user_patterns_can_add_blocks() -> None:
    """The user can add their own blocked_processes — additive to the system."""
    cfg = AwarenessConfig.default()

    def user_patterns() -> tuple[list[str], list[str], list[str]]:
        # (blocked_processes, blocked_title_patterns, allowed_processes)
        return (["secretapp.exe"], [], [])

    pf = PrivacyFilter(cfg, user_patterns_fn=user_patterns)
    allowed, reason = pf.is_allowed(
        window_title="My Secret Tool",
        process_name="secretapp.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_process:secretapp.exe")


def test_user_patterns_cannot_remove_system_blocks() -> None:
    """User patterns are ADDITIVE — an empty user list leaves the system
    defaults active. 1password stays blocked even if the user adds nothing."""
    cfg = AwarenessConfig.default()

    def user_patterns() -> tuple[list[str], list[str], list[str]]:
        return ([], [], [])  # user deletes nothing; can't anyway

    pf = PrivacyFilter(cfg, user_patterns_fn=user_patterns)
    allowed, _ = pf.is_allowed(
        window_title="My Vault",
        process_name="1password.exe",
    )
    assert allowed is False  # system default 1password* still applies


def test_user_patterns_callback_errors_are_swallowed() -> None:
    """If user_patterns_fn raises: treat it like an empty list, don't crash.
    Mirrors jarvis.safety.risk_tier._collect_patterns try/except."""
    cfg = AwarenessConfig.default()

    def broken() -> tuple[list[str], list[str], list[str]]:
        raise RuntimeError("user-toml-broken")

    pf = PrivacyFilter(cfg, user_patterns_fn=broken)
    # Doesn't crash; system defaults apply normally
    allowed, _ = pf.is_allowed(
        window_title="Untitled - Notepad",
        process_name="notepad.exe",
    )
    assert allowed is True


# --- reason string format (plan §4 smoke spec) ------------------------------

def test_reason_format_for_blocked_title() -> None:
    """reason format: matched_blocked_title:<pattern>."""
    pf = _filter()
    _, reason = pf.is_allowed(
        window_title="PayPal Login",
        process_name="firefox.exe",
    )
    assert reason == "matched_blocked_title:*PayPal*"


def test_reason_format_for_allowed_process() -> None:
    """reason format: matched_allowed_process:<pattern>."""
    pf = _filter()
    _, reason = pf.is_allowed(
        window_title="Untitled-1 - Visual Studio Code",
        process_name="code.exe",
    )
    assert reason == "matched_allowed_process:code.exe"


def test_case_insensitive_matching() -> None:
    """Patterns match case-insensitively (like risk_tier.py:105)."""
    pf = _filter()
    allowed, _ = pf.is_allowed(
        window_title="Meine SPARKASSE Online-Banking Sitzung",  # i18n-allow
        process_name="firefox.exe",
    )
    assert allowed is False
