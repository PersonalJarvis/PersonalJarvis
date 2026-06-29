"""Hard-refuse path patterns — the defense-in-depth deny layer.

Extracted from ``registry.py`` so both the registry AND the schema introspector
can consult it without an import cycle (the introspector must skip these paths
while walking; the registry delegates ``is_forbidden`` here).

Forbidden paths today are the **secrets / privileged sections** — never readable
or writable via self-mod or the Control API (the original Plan-§AP-9 set).

**Self-lockout (Wave 1.2):** the goal was to additionally refuse settings that
would disable the very channel needed to reverse them by voice. The honest
finding during implementation: the schema exposes almost no such switch, and the
architecture already closes the real vectors —

* STT/TTS have no enable flags (you cannot turn them off via config at all);
* ``brain.providers.enabled`` is a ``list`` → the schema walker skips it, so the
  provider list can never be emptied by voice (the "kill the active brain"
  vector); guarded by ``test_self_lockout_provider_list_not_mutable``;
* a config that breaks the boot is caught by the writer's reload-test + auto
  rollback, and a chat/Settings fallback channel always remains.

So no broad self-lockout wildcards are added here — they would only block
legitimate commands ("turn the wake word off") that are reversible via chat.
If a genuine self-lockout scalar appears in the schema later, add its exact
path (not a wildcard) to ``FORBIDDEN_PATTERNS``.
"""
from __future__ import annotations

from fnmatch import fnmatch

FORBIDDEN_PATTERNS: tuple[str, ...] = (
    # --- Secrets / privileged sections (Plan-§AP-9) ---
    "security.*",
    "safety.*",  # risk-tier whitelist/blacklist — never via self-mod or Control API
    "mcp_server.*",
    "harness.*",
    "*_api_key",
    "*_token",
    "*_secret",
    "*_password",
    "*_password_hash",
    "*_credential",
)


def is_forbidden(path: str) -> bool:
    """True if ``path`` belongs to a protected / self-lockout section."""
    return any(fnmatch(path, pattern) for pattern in FORBIDDEN_PATTERNS)


__all__ = ["FORBIDDEN_PATTERNS", "is_forbidden"]
