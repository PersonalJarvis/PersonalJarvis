"""Block list for protected file paths (Risk Register #7 + #6).

Two levels:
- `is_blocked(path)`: single path against glob patterns.
- `filter_diff_paths(diff_text)`: extracts changed files from a
  `git diff --git` block, returns only the blocked ones.
- `check_prompt_for_blocked_paths(prompt)`: scans a mission prompt for
  path references that would be blocked (pre-spawn heuristic).

Patterns follow `fnmatch` glob syntax. The default list covers SSH/AWS/.env/SSL keys.
Extensible via `[phase6.safety].extra_blocked_globs` in jarvis.toml.

Important: Path-Guard is the SECOND line of defense. The Phase-5
`tool_executor` already has a risk-tier blacklist at the tool level; here
we guard specifically against worker diff outputs (mission level).
"""
from __future__ import annotations

import fnmatch
import re
from typing import Final

# Glob patterns against normalized POSIX paths (forward-slash; lowercase
# is not required — fnmatch is case-sensitive in POSIX style; we
# normalize backslashes to forward slashes).
DEFAULT_BLOCKED_GLOBS: Final[tuple[str, ...]] = (
    # SSH
    "**/.ssh",
    "**/.ssh/**",
    "**/id_rsa",
    "**/id_rsa.*",
    "**/id_ed25519",
    "**/id_ed25519.*",
    "**/id_ecdsa",
    "**/id_ecdsa.*",
    "**/known_hosts",
    "**/authorized_keys",
    # AWS
    "**/.aws",
    "**/.aws/**",
    # GitHub CLI
    "**/.config/gh",
    "**/.config/gh/**",
    "**/.config/gh/hosts.yml",
    # Generic credentials
    "**/credentials",
    "**/credentials.*",
    "**/.netrc",
    # Env-Files
    "**/.env",
    "**/.env.*",
    # Cert/Keys
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    # Windows DPAPI / Vault
    "**/AppData/Roaming/Microsoft/Crypto/**",
    "**/AppData/Local/Microsoft/Vault/**",
    # Browser-Profile (Cookies / Saved-Logins)
    "**/AppData/Local/Google/Chrome/User Data/**/Login Data*",
    "**/AppData/Roaming/Mozilla/Firefox/Profiles/**/logins.json",
)


def _normalize(path: str) -> str:
    """Normalize a path to POSIX style for fnmatch."""
    # Backslashes -> forward slashes
    p = path.replace("\\", "/")
    # Remove double slashes
    while "//" in p:
        p = p.replace("//", "/")
    return p


def _basename_pattern(pattern: str) -> str:
    """Strip the leading ``**/`` so fnmatch also matches bare basenames.

    ``fnmatch.translate('**/.env')`` produces ``.*/\\.env\\Z`` — the slash is
    required, meaning ``.env`` (without a parent) would NOT match. We build
    a parallel pattern ``.env`` for basename comparison.
    """
    if pattern.startswith("**/"):
        return pattern[3:]
    return pattern


def is_blocked(
    path: str,
    *,
    extra_globs: tuple[str, ...] = (),
) -> bool:
    """True if `path` matches DEFAULT_BLOCKED_GLOBS or extra_globs.

    We match against both the full path AND the basename (last path
    segment). Rationale: fnmatch's ``**`` requires a slash, so a bare
    ``.env`` (without a parent) would NOT match ``**/.env``. We strip
    ``**/`` for the basename comparison (see ``_basename_pattern``).
    """
    norm = _normalize(path)
    basename = norm.rsplit("/", 1)[-1] if "/" in norm else norm

    for pattern in DEFAULT_BLOCKED_GLOBS:
        if fnmatch.fnmatch(norm, pattern):
            return True
        if fnmatch.fnmatch(basename, _basename_pattern(pattern)):
            return True
    for pattern in extra_globs:
        if fnmatch.fnmatch(norm, pattern):
            return True
        if fnmatch.fnmatch(basename, _basename_pattern(pattern)):
            return True
    return False


# Regex for extracting filenames from `diff --git a/path b/path` headers.
# Accepts both Unix and Windows paths. `(?P<a>...)` and `(?P<b>...)`
# capture the two paths independently (rename detection).
_DIFF_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^diff --git\s+a/(?P<a>\S+)\s+b/(?P<b>\S+)\s*$",
    re.MULTILINE,
)


def filter_diff_paths(
    diff_text: str,
    *,
    extra_globs: tuple[str, ...] = (),
) -> list[str]:
    """Extract all changed files from a `git diff` block,
    returning only the blocked ones.

    Args:
        diff_text: Output of `git diff HEAD` or `git diff <base>`.
        extra_globs: Additional fnmatch patterns (from jarvis.toml).

    Returns:
        List of blocked paths (deduplicated, sorted).
    """
    if not diff_text:
        return []
    paths: set[str] = set()
    for match in _DIFF_HEADER_RE.finditer(diff_text):
        for key in ("a", "b"):
            p = match.group(key)
            if is_blocked(p, extra_globs=extra_globs):
                paths.add(p)
    return sorted(paths)


# Heuristic for pre-spawn: does not match every substring (which would cause
# false positives on "reading the ssh-config" — no concrete path), but only
# strings that look like file paths (slash, dot, backslash).
_PATH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:[~./]|\b[A-Za-z]:\\)[\w\-./\\]+",
)


def check_prompt_for_blocked_paths(
    prompt: str,
    *,
    extra_globs: tuple[str, ...] = (),
) -> list[str]:
    """Scan a mission prompt for path tokens that would be blocked.

    Conservative: matches only strings that LOOK LIKE a path (slash, dot,
    or drive letter), NOT bare word substrings. Intended to avoid false
    positives on harmless mentions ("the env variable").

    Returns:
        List of blocked paths found (deduplicated).
    """
    if not prompt:
        return []
    found: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(prompt):
        token = match.group(0).rstrip(".,;:")
        if is_blocked(token, extra_globs=extra_globs):
            found.add(token)
    return sorted(found)


__all__ = [
    "DEFAULT_BLOCKED_GLOBS",
    "check_prompt_for_blocked_paths",
    "filter_diff_paths",
    "is_blocked",
]
