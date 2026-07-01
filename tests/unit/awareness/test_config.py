"""Tests for jarvis.awareness.config.AwarenessConfig.

A0 scope:
- The Pydantic model loads with defaults (default() helper).
- The default blacklist matches the plan §4 wording.
- Backward compat: jarvis.toml without an [awareness] block loads cleanly.
- Custom TOML overrides the defaults.
"""
from __future__ import annotations

import textwrap
import tomllib

from jarvis.awareness.config import AwarenessConfig


def test_default_loads_clean() -> None:
    """AwarenessConfig.default() with no arguments, no errors."""
    cfg = AwarenessConfig.default()
    assert cfg.enabled is True
    assert cfg.privacy is not None
    assert cfg.watchers is not None
    assert cfg.quotas is not None


def test_default_blacklist_matches_plan_section_4() -> None:
    """The default blacklist contains exactly the §4 patterns."""
    cfg = AwarenessConfig.default()
    blocked_procs = cfg.privacy.blocked_processes
    assert "1password*" in blocked_procs
    assert "keepass*" in blocked_procs
    assert "bitwarden*" in blocked_procs
    assert "lastpass*" in blocked_procs

    blocked_titles = cfg.privacy.blocked_title_patterns
    for pattern in (
        "*Banking*", "*PayPal*", "*Stripe*Dashboard*",
        "*Sparkasse*", "*Postbank*", "*Online-Banking*",
        "*Passwort*", "*Password*Manager*",
        "*Inkognito*", "*Private Browsing*",
    ):
        assert pattern in blocked_titles, f"plan pattern missing: {pattern}"


def test_default_allowed_processes_matches_plan() -> None:
    """The default allowlist contains coding apps from §4."""
    cfg = AwarenessConfig.default()
    for proc in ("code.exe", "cursor.exe", "windsurf.exe",
                 "WindowsTerminal.exe", "pwsh.exe", "cmd.exe"):
        assert proc in cfg.privacy.allowed_processes


def test_default_when_unknown_is_hybrid() -> None:
    """D-A1 default: hybrid strategy."""
    cfg = AwarenessConfig.default()
    assert cfg.privacy.default_when_unknown == "block_for_browsers_allow_for_others"


def test_watchers_defaults_for_a1_forward_compat() -> None:
    """Watchers defaults are defined here, used in A1."""
    cfg = AwarenessConfig.default()
    assert cfg.watchers.enable_window is True
    assert cfg.watchers.enable_idle is True
    assert cfg.watchers.idle_threshold_minutes == 5


def test_quotas_defaults() -> None:
    """Quotas defaults: 50 MiB / 1000 episodes."""
    cfg = AwarenessConfig.default()
    assert cfg.quotas.max_bytes == 50 * 1024 * 1024
    assert cfg.quotas.max_episodes == 1000


def test_loads_from_toml_overrides_defaults() -> None:
    """Custom TOML overrides defaults field by field."""
    raw = textwrap.dedent("""
        enabled = false

        [privacy]
        blocked_processes = ["customapp*"]
        blocked_title_patterns = ["*MyBank*"]
        allowed_processes = ["myeditor.exe"]
        default_when_unknown = "block_for_browsers_allow_for_others"

        [watchers]
        enable_window = false
        enable_idle = true
        idle_threshold_minutes = 10

        [quotas]
        max_bytes = 1024
        max_episodes = 5
    """).strip()
    data = tomllib.loads(raw)
    cfg = AwarenessConfig(**data)
    assert cfg.enabled is False
    assert cfg.privacy.blocked_processes == ["customapp*"]
    assert cfg.watchers.enable_window is False
    assert cfg.watchers.idle_threshold_minutes == 10
    assert cfg.quotas.max_bytes == 1024


def test_partial_toml_keeps_defaults_for_missing_keys() -> None:
    """Backward compat: only some fields set → the rest stays default."""
    data = tomllib.loads('enabled = false\n')
    cfg = AwarenessConfig(**data)
    assert cfg.enabled is False
    # Privacy defaults were NOT changed
    assert "1password*" in cfg.privacy.blocked_processes
