"""Pydantic schema for the macOS + Linux whitelisted admin operations (AD-12).

This module is the cross-platform sibling of ``jarvis.admin.schema`` (the
Windows op vocabulary). Every model here subclasses ``_AdminOpBase``
(``frozen=True`` + ``extra="forbid"``) so it inherits the exact same strict
validation contract as the Windows ops — no extra fields, immutable instances.

Safety mandate (identical to ``schema.py``):

- **Pattern-validated argv only.** Every user-controlled field carries a regex
  that rejects anything that is not identifier-safe. A malicious payload such as
  ``package="git; rm -rf /"`` MUST fail validation before it ever reaches the
  executor — the regex is the first injection defense and the
  ``shell=False`` + list-argv subprocess contract in ``executor.py`` is the
  second.
- **No free-form shell strings ever.** There is no field anywhere in this module
  that is passed verbatim to a shell.

The executor (`jarvis.admin.executor`) builds a validated argv list for each op
(e.g. ``["apt-get", "install", "-y", op.package]``) and runs it with
``shell=False``, exactly the Windows executor's contract.

Destructive ops (``apt_remove``, ``brew_remove``, ``ufw_remove``,
``launchctl`` ``unload``, ``systemctl`` ``stop``/``disable``, and the shared
``write_protected_path``) are registered in ``schema.DESTRUCTIVE_OPS`` so the
per-action approval gate (`client.py`) fires identically across OSes.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

# ``_AdminOpBase`` carries ``frozen=True`` + ``extra="forbid"``. Reuse it so the
# cross-platform ops inherit the same strict validation as the Windows ops.
# ``WriteProtectedPathOp`` is shared verbatim — only the *paths* differ per OS
# (``/etc/...``, ``/usr/...`` on Linux; ``/Library/...``, ``/Applications/...``
# on macOS) and the regex-free path field is already length-bounded.
from .schema import _AdminOpBase

# ---------------------------------------------------------------------
# Shared field validators (pattern-validated argv only)
# ---------------------------------------------------------------------

# Debian/Ubuntu package name: lowercase start, then lowercase alnum plus the
# three characters Debian policy permits in a package name (``+``, ``-``, ``.``).
# Deliberately excludes spaces, slashes, semicolons, and every shell metachar —
# ``"git; rm -rf /"`` cannot match.
_APT_PACKAGE = Field(
    min_length=1,
    max_length=128,
    pattern=r"^[a-z0-9][a-z0-9+\-.]{0,127}$",
)

# systemd unit name: alphanumerics plus ``@ . _ -`` and an optional ``.<suffix>``
# (``nginx``, ``ssh.service``, ``getty@tty1.service``). No spaces, no slashes,
# no metacharacters.
_SYSTEMD_UNIT = Field(
    min_length=1,
    max_length=256,
    pattern=r"^[A-Za-z0-9@._\-]{1,247}(?:\.[A-Za-z]{1,8})?$",
)

# Homebrew formula/cask name: lowercase start, then lowercase alnum plus
# ``+ - . @`` (covers versioned formulae like ``python@3.11`` and casks). No
# slashes/spaces/metacharacters.
_BREW_FORMULA = Field(
    min_length=1,
    max_length=128,
    pattern=r"^[a-z0-9][a-z0-9+\-.@]{0,127}$",
)

# launchctl service label: reverse-DNS-style identifier
# (``com.apple.Spotlight``, ``org.jarvis.helper``). Dots, dashes, underscores
# only — no spaces/metacharacters.
_LAUNCHD_LABEL = Field(
    min_length=1,
    max_length=256,
    pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,254}[A-Za-z0-9]$",
)


# ---------------------------------------------------------------------
# Linux — apt (package install / remove)
# ---------------------------------------------------------------------

class AptInstallOp(_AdminOpBase):
    """``apt-get install -y <package>`` — install a Debian/Ubuntu package."""
    type: Literal["apt_install"] = "apt_install"
    package: str = _APT_PACKAGE


class AptRemoveOp(_AdminOpBase):
    """``apt-get remove -y <package>`` — destructive (uninstalls software)."""
    type: Literal["apt_remove"] = "apt_remove"
    package: str = _APT_PACKAGE


# ---------------------------------------------------------------------
# Linux — systemctl (service control)
# ---------------------------------------------------------------------

class SystemctlOp(_AdminOpBase):
    """``systemctl <action> <unit>`` — control a systemd unit.

    ``stop`` and ``disable`` are destructive (they take a service down /
    out of boot); the executor + ``DESTRUCTIVE_OPS`` treat this op as
    destructive so the per-action approval gate always fires (conservative —
    ``start``/``enable``/``restart`` are gated too, mirroring the Windows
    ``remove_service`` being the only destructive service op).
    """
    type: Literal["systemctl"] = "systemctl"
    action: Literal["start", "stop", "enable", "disable", "restart"]
    unit: str = _SYSTEMD_UNIT


# ---------------------------------------------------------------------
# Linux — ufw (firewall rule)
# ---------------------------------------------------------------------

class UfwRuleOp(_AdminOpBase):
    """``ufw allow|deny <port>/<proto>`` — add a UFW firewall rule."""
    type: Literal["ufw_rule"] = "ufw_rule"
    action: Literal["allow", "deny"] = "allow"
    port: int = Field(ge=1, le=65535)
    proto: Literal["tcp", "udp"] = "tcp"


class UfwRemoveOp(_AdminOpBase):
    """``ufw delete allow|deny <port>/<proto>`` — destructive (removes a rule)."""
    type: Literal["ufw_remove"] = "ufw_remove"
    action: Literal["allow", "deny"] = "allow"
    port: int = Field(ge=1, le=65535)
    proto: Literal["tcp", "udp"] = "tcp"


# ---------------------------------------------------------------------
# macOS — Homebrew (package install / remove)
# ---------------------------------------------------------------------

class BrewInstallOp(_AdminOpBase):
    """``brew install <formula>`` — install a Homebrew formula/cask."""
    type: Literal["brew_install"] = "brew_install"
    formula: str = _BREW_FORMULA


class BrewRemoveOp(_AdminOpBase):
    """``brew uninstall <formula>`` — destructive (uninstalls software)."""
    type: Literal["brew_remove"] = "brew_remove"
    formula: str = _BREW_FORMULA


# ---------------------------------------------------------------------
# macOS — launchctl (service control)
# ---------------------------------------------------------------------

class LaunchctlOp(_AdminOpBase):
    """``launchctl <action> <label>`` — control a launchd service.

    ``unload`` is destructive (it removes a running service); the executor +
    ``DESTRUCTIVE_OPS`` treat this op as destructive so the per-action approval
    gate always fires.
    """
    type: Literal["launchctl"] = "launchctl"
    action: Literal["load", "unload", "enable", "disable"]
    label: str = _LAUNCHD_LABEL


# ---------------------------------------------------------------------
# Unix op union + metadata (consumed by schema.py to extend the superset)
# ---------------------------------------------------------------------

# The discriminated union of every Unix (Linux + macOS) op. ``schema.py`` folds
# this into the platform-superset ``AdminOperation`` so a single helper process
# can decode any op and the executor dispatches per OS. ``WriteProtectedPathOp``
# is intentionally NOT repeated here — it already lives in ``schema.py`` and is a
# member of the superset union for every OS.
UnixAdminOperation = Annotated[
    (
        AptInstallOp
        | AptRemoveOp
        | SystemctlOp
        | UfwRuleOp
        | UfwRemoveOp
        | BrewInstallOp
        | BrewRemoveOp
        | LaunchctlOp
    ),
    Field(discriminator="type"),
]


# Every Unix op type string. ``write_protected_path`` is shared and lives in
# ``schema.ADMIN_OPERATION_TYPES`` already.
UNIX_ADMIN_OPERATION_TYPES: tuple[str, ...] = (
    "apt_install",
    "apt_remove",
    "systemctl",
    "ufw_rule",
    "ufw_remove",
    "brew_install",
    "brew_remove",
    "launchctl",
)


# Unix ops that always require per-action approval (Mandat §6.2). Note that
# ``systemctl`` and ``launchctl`` are listed wholesale: a discriminated union
# member maps to exactly one op-type string, so the approval gate (which keys on
# the op-type string) cannot distinguish ``systemctl start`` from
# ``systemctl stop``. We therefore gate the whole op-type conservatively — the
# approval gate is anti-confirmation-fatigue at the *capability* layer, not the
# argument layer. ``write_protected_path`` is shared and already in
# ``schema.DESTRUCTIVE_OPS``.
UNIX_DESTRUCTIVE_OPS: frozenset[str] = frozenset({
    "apt_remove",
    "brew_remove",
    "ufw_remove",
    "systemctl",       # covers stop / disable (and start/enable/restart)
    "launchctl",       # covers unload (and load/enable/disable)
})


__all__ = [
    "AptInstallOp",
    "AptRemoveOp",
    "SystemctlOp",
    "UfwRuleOp",
    "UfwRemoveOp",
    "BrewInstallOp",
    "BrewRemoveOp",
    "LaunchctlOp",
    "UnixAdminOperation",
    "UNIX_ADMIN_OPERATION_TYPES",
    "UNIX_DESTRUCTIVE_OPS",
]
