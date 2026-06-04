"""Tests fuer jarvis.awareness.privacy.PrivacyFilter.

Plan §4 Smoke-Erwartungen + Hybrid-Default-Verhalten + User-Patterns-Additivitaet.

PrivacyFilter spiegelt jarvis.safety.risk_tier: SYSTEM-Patterns sind hart,
USER kann nur additiv blocken (nie System-Defaults entfernen). Reihenfolge:
BLOCK > ALLOW > Default-Hybrid (block-for-browsers).
"""
from __future__ import annotations

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.privacy import PrivacyFilter


def _filter() -> PrivacyFilter:
    return PrivacyFilter(AwarenessConfig.default())


# --- Plan §4 Smoke-Test (verbindlich) --------------------------------------

def test_blocks_banking_title() -> None:
    """Plan-Smoke 1: Sparkasse-Online-Banking → BLOCKED, reason matcht."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Sparkasse Online-Banking - Mozilla Firefox",
        process_name="firefox.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_title:")
    # Ein Banking-Pattern muss matchen — entweder *Banking* oder *Sparkasse* oder *Online-Banking*
    assert any(p in reason for p in ("*Banking*", "*Sparkasse*", "*Online-Banking*"))


def test_allows_vscode() -> None:
    """Plan-Smoke 2: VS Code → ALLOWED, reason matcht."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="pipeline.py - jarvis - Visual Studio Code",
        process_name="code.exe",
    )
    assert allowed is True
    assert reason == "matched_allowed_process:code.exe"


# --- Hybrid-Default (D-A1) -------------------------------------------------

def test_browser_without_explicit_block_still_blocked_via_hybrid() -> None:
    """D-A1: Browser ohne Title-Match → BLOCKED (default block_for_browsers)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Wikipedia — The Free Encyclopedia",
        process_name="firefox.exe",
    )
    assert allowed is False
    assert reason == "default_block_for_browser"


def test_unknown_non_browser_allowed_via_hybrid() -> None:
    """D-A1: Notepad o.ae. → ALLOWED (default allow_for_others)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Untitled - Notepad",
        process_name="notepad.exe",
    )
    assert allowed is True
    assert reason == "default_allow_for_unknown"


def test_chrome_blocked_by_hybrid() -> None:
    """Chrome zaehlt als Browser → Hybrid-Default blockt."""
    pf = _filter()
    allowed, _ = pf.is_allowed(
        window_title="GitHub - Mozilla Firefox",
        process_name="chrome.exe",
    )
    assert allowed is False


# --- BLOCK trumpft ALLOW + Reihenfolge -------------------------------------

def test_blocked_process_trumps_allowed_process() -> None:
    """1password-Title in code.exe → BLOCK gewinnt (process-blacklist greift)."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="My Vault - 1Password",
        process_name="1password.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_process:1password*")


def test_blocked_title_trumps_allowed_process() -> None:
    """Banking-Title in code.exe (z.B. PDF-View) → Title-Blacklist gewinnt."""
    pf = _filter()
    allowed, reason = pf.is_allowed(
        window_title="Sparkasse Statement.pdf - Code",
        process_name="code.exe",
    )
    assert allowed is False
    assert reason.startswith("matched_blocked_title:")


# --- User-Patterns sind additiv --------------------------------------------

def test_user_patterns_can_add_blocks() -> None:
    """User kann eigene blocked_processes ergaenzen — additiv zum System."""
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
    """User-Patterns sind ADDITIV — leere User-Liste laesst System-Defaults
    aktiv. 1password bleibt blockiert auch wenn User nichts hinzufuegt."""
    cfg = AwarenessConfig.default()

    def user_patterns() -> tuple[list[str], list[str], list[str]]:
        return ([], [], [])  # User loescht nichts; kann auch nicht

    pf = PrivacyFilter(cfg, user_patterns_fn=user_patterns)
    allowed, _ = pf.is_allowed(
        window_title="My Vault",
        process_name="1password.exe",
    )
    assert allowed is False  # System-Default 1password* greift weiterhin


def test_user_patterns_callback_errors_are_swallowed() -> None:
    """Wenn user_patterns_fn raised: behandeln wie leere Liste, nicht crashen.
    Spiegelt jarvis.safety.risk_tier._collect_patterns try/except."""
    cfg = AwarenessConfig.default()

    def broken() -> tuple[list[str], list[str], list[str]]:
        raise RuntimeError("user-toml-broken")

    pf = PrivacyFilter(cfg, user_patterns_fn=broken)
    # Crasht nicht; System-Defaults greifen normal
    allowed, _ = pf.is_allowed(
        window_title="Untitled - Notepad",
        process_name="notepad.exe",
    )
    assert allowed is True


# --- reason-String-Format (Plan §4 Smoke-Spec) -----------------------------

def test_reason_format_for_blocked_title() -> None:
    """reason-Format: matched_blocked_title:<pattern>."""
    pf = _filter()
    _, reason = pf.is_allowed(
        window_title="PayPal Login",
        process_name="firefox.exe",
    )
    assert reason == "matched_blocked_title:*PayPal*"


def test_reason_format_for_allowed_process() -> None:
    """reason-Format: matched_allowed_process:<pattern>."""
    pf = _filter()
    _, reason = pf.is_allowed(
        window_title="Untitled-1 - Visual Studio Code",
        process_name="code.exe",
    )
    assert reason == "matched_allowed_process:code.exe"


def test_case_insensitive_matching() -> None:
    """Patterns matchen case-insensitive (wie risk_tier.py:105)."""
    pf = _filter()
    allowed, _ = pf.is_allowed(
        window_title="Meine SPARKASSE Online-Banking Sitzung",
        process_name="firefox.exe",
    )
    assert allowed is False
