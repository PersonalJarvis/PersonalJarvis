"""Locate the freshest live Claude CLI OAuth login across config dirs.

The ``claude`` CLI stores its subscription OAuth bearer in
``<config dir>/.credentials.json`` and refreshes it in place — but only in
the config dir its sessions actually run with. The default is ``~/.claude``;
``CLAUDE_CONFIG_DIR`` (the CLI's official override) and multi-profile
managers pin sessions elsewhere, so the default file silently expires in
place while the real, freshly-refreshed login lives in another directory.

Reading ONLY ``~/.claude`` then reports "subscription login expired", the
Jarvis-Agents health banner declares the worker unavailable, and every heavy
mission diverts to another family — although a perfectly live login sits on
disk (2026-07-10 incident on a profile-manager-driven host; the same class as
the 2026-07-06 expired-in-place incident, one directory further left).

One shared rule fixes every reader: enumerate the known candidate config
dirs and let the FRESHEST live login win. Consumers:

* ``jarvis.missions.isolation.env`` — the token injected into isolated
  mission workers and the worker-viability gate;
* ``jarvis.claude_auth.ClaudeAuthService`` — the subscription card in the
  API-Keys view.

Pure stdlib + pathlib, cross-platform, never raises (CLOUD.md Rule #1): a
missing directory, file, or profile manager is simply not a candidate.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Minimum remaining lifetime for a login to count as live. An isolated
# mission worker holds no refresh token, so a bearer dying seconds after the
# spawn would 401 mid-mission; the status card shares the same rule so the UI
# and the worker never disagree about "connected".
OAUTH_EXPIRY_SLACK_S: float = 120.0

# Multi-profile managers keep one Claude config dir per profile and launch
# every session with CLAUDE_CONFIG_DIR pointing at it — on such a host
# nothing ever refreshes ~/.claude again, so these profiles are where the
# live login actually is. Globbed relative to the home dir, best-effort;
# empty wherever the manager is not installed.
_PROFILE_MANAGER_GLOBS: tuple[str, ...] = (".bridgespace/ai-profiles/claude/*",)


def claude_config_dirs() -> list[Path]:
    """Candidate Claude CLI config dirs, most authoritative first (test seam).

    Order: ``$CLAUDE_CONFIG_DIR`` (the CLI's official override, when set) →
    the ``~/.claude`` default → per-profile dirs of known profile managers.
    Existence is NOT required; readers treat unreadable files as absent.
    """
    dirs: list[Path] = []
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir).expanduser())
    home = Path.home()
    dirs.append(home / ".claude")
    for pattern in _PROFILE_MANAGER_GLOBS:
        try:
            dirs.extend(sorted(p for p in home.glob(pattern) if p.is_dir()))
        except OSError:
            continue
    # Path.__eq__ is already case-insensitive on Windows; dedupe keeps the
    # first (most authoritative) occurrence.
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


@dataclass(frozen=True)
class ClaudeOAuthSnapshot:
    """The winning Claude OAuth login across all candidate config dirs.

    ``access_token`` is only set while ``status == "valid"`` — an expired
    bearer is never handed out (injecting it is a guaranteed 401).
    ``config_dir`` names the directory whose credentials file won, so
    callers can read sibling files (e.g. the ``.claude.json`` identity)
    from the SAME login, not a different profile's.
    """

    status: Literal["valid", "expired", "absent"]
    access_token: str | None = None
    subscription_type: str | None = None
    config_dir: Path | None = None
    expires_s: float | None = None  # epoch seconds; None == no expiry recorded


def _parse_oauth_file(
    path: Path,
) -> tuple[str, str | None, float | None] | None:
    """Tolerant parse of one ``.credentials.json``.

    Returns ``(access_token, subscription_type, expires_s)`` for a present
    ``sk-ant-oat`` bearer, else ``None``. Only OAuth bearers count — a classic
    API key in the bearer slot is not a subscription login.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not (isinstance(token, str) and token.startswith("sk-ant-oat")):
        return None
    sub_type = oauth.get("subscriptionType")
    sub_type = sub_type if isinstance(sub_type, str) and sub_type else None
    expires_at = oauth.get("expiresAt")
    expires_s: float | None = None
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        # `claude` writes epoch milliseconds; tolerate seconds defensively.
        expires_s = expires_at / 1000.0 if expires_at > 1e12 else float(expires_at)
    return token, sub_type, expires_s


def freshest_claude_oauth(*, now_fn: Callable[[], float] = time.time) -> ClaudeOAuthSnapshot:
    """Scan every candidate config dir; the freshest LIVE login wins.

    - ``"valid"``: at least one non-expired bearer exists; the one with the
      farthest ``expiresAt`` is returned (no ``expiresAt`` at all stays
      fail-open for older credential shapes and sorts as farthest).
    - ``"expired"``: bearers exist but every one has died in place; the
      least-stale one is the reference (its tier feeds the UI message).
    - ``"absent"``: no readable ``sk-ant-oat`` bearer anywhere.

    Cheap and offline — a handful of small file reads, never a subprocess or
    network probe, so health checks may call it freely.
    """
    horizon = now_fn() + OAUTH_EXPIRY_SLACK_S
    best_valid: tuple[float, ClaudeOAuthSnapshot] | None = None
    best_expired: tuple[float, ClaudeOAuthSnapshot] | None = None
    for config_dir in claude_config_dirs():
        parsed = _parse_oauth_file(config_dir / ".credentials.json")
        if parsed is None:
            continue
        token, sub_type, expires_s = parsed
        if expires_s is None or expires_s > horizon:
            sort_key = float("inf") if expires_s is None else expires_s
            if best_valid is None or sort_key > best_valid[0]:
                best_valid = (
                    sort_key,
                    ClaudeOAuthSnapshot(
                        status="valid",
                        access_token=token,
                        subscription_type=sub_type,
                        config_dir=config_dir,
                        expires_s=expires_s,
                    ),
                )
        elif best_expired is None or expires_s > best_expired[0]:
            best_expired = (
                expires_s,
                ClaudeOAuthSnapshot(
                    status="expired",
                    subscription_type=sub_type,
                    config_dir=config_dir,
                    expires_s=expires_s,
                ),
            )
    if best_valid is not None:
        return best_valid[1]
    if best_expired is not None:
        return best_expired[1]
    return ClaudeOAuthSnapshot(status="absent")


__all__ = [
    "OAUTH_EXPIRY_SLACK_S",
    "ClaudeOAuthSnapshot",
    "claude_config_dirs",
    "freshest_claude_oauth",
]
