"""The servers this PC's own ``ssh`` already knows, offered in the add dialog.

Portions adapted from pingdotgg/t3code @ 12069ee (packages/ssh/src/config.ts:
alias collection with ``Include`` globs, ``known_hosts`` parsing), MIT License,
Copyright (c) 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.

Two sources, read from the user's home on every OS (``~`` is ``%USERPROFILE%``
on Windows):

* ``~/.ssh/config`` (and every file it ``Include``\\ s): each concrete ``Host``
  alias, resolved the way OpenSSH resolves it — the FIRST value a matching
  block gives an option wins, wildcard blocks such as ``Host *`` included.
* ``~/.ssh/known_hosts``: hosts this PC has logged in to before (hashed lines
  are unreadable by design and skipped).

Nothing here connects anywhere or changes a file. ``Match`` blocks need a
live connection to evaluate and are skipped; a host that needs a jump host
(``ProxyJump``/``ProxyCommand``) is listed but marked, because the app's own
SSH client does not route through one.
"""

from __future__ import annotations

import fnmatch
import getpass
import glob
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

HostSource = Literal["ssh_config", "known_hosts"]

#: ``Include`` may nest; OpenSSH stops at 16 levels.
_MAX_INCLUDE_DEPTH = 16
#: A config is a handful of lines; anything bigger is not one.
_MAX_FILE_BYTES = 1_000_000
_PORT_RE = re.compile(r"^\d{1,5}$")
#: Git forges show up in ``known_hosts`` from every ``git clone``; they are not
#: machines anyone adds as a computer.
_GIT_FORGES = frozenset(
    {
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "codeberg.org",
        "ssh.dev.azure.com",
        "vs-ssh.visualstudio.com",
        "git.sr.ht",
        "ssh.github.com",
        "altssh.gitlab.com",
        "altssh.bitbucket.org",
    }
)


@dataclass(frozen=True)
class SshConfigHost:
    """One server this PC knows, resolved to what a login needs."""

    alias: str
    host: str
    port: int
    username: str | None
    source: HostSource
    #: Private key files the config names for this host (that exist).
    identity_files: tuple[str, ...] = ()
    #: Reached through a jump host; the app cannot connect to it directly.
    needs_proxy: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "source": self.source,
            "has_identity_file": bool(self.identity_files),
            "needs_proxy": self.needs_proxy,
        }


@dataclass
class _Block:
    patterns: tuple[str, ...]
    options: dict[str, list[str]] = field(default_factory=dict)
    #: A ``Match`` block: it needs a live connection to evaluate, so never applies.
    match: bool = False

    def applies_to(self, alias: str) -> bool:
        if self.match:
            return False
        name = alias.lower()
        positive = False
        for pattern in self.patterns:
            negated = pattern.startswith("!")
            if fnmatch.fnmatchcase(name, pattern.lstrip("!").lower()):
                if negated:
                    return False
                positive = True
        return positive


def home_dir() -> Path:
    return Path(os.path.expanduser("~"))


def _strip_comment(line: str) -> str:
    index = line.find("#")
    return (line[:index] if index >= 0 else line).strip()


def _split(line: str) -> list[str]:
    """``Key value`` or ``Key=value``; double quotes group a value with spaces."""
    line = re.sub(r"^(\S+?)\s*=\s*", r"\1 ", line, count=1)
    parts: list[str] = []
    for match in re.finditer(r'"([^"]*)"|(\S+)', line):
        parts.append(match.group(1) if match.group(1) is not None else match.group(2))
    return parts


def _is_pattern(value: str) -> bool:
    return "*" in value or "?" in value or value.startswith("!")


def _expand_home(value: str, home: Path) -> str:
    if value == "~" or value.startswith(("~/", "~\\")):
        return str(home) + value[1:]
    return value


def _include_paths(pattern: str, home: Path) -> list[Path]:
    expanded = _expand_home(pattern, home)
    path = Path(expanded)
    if not path.is_absolute():
        path = home / ".ssh" / expanded
    return [Path(p) for p in sorted(glob.glob(str(path))) if Path(p).is_file()]


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            log.info("computers: %s is too large to be an SSH config; skipped", path)
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Unreadable or missing simply means nothing to offer from it.
        return None


def parse_config(path: Path, home: Path) -> list[_Block]:
    """Every ``Host``/``Match`` block of ``path`` and its includes, in file order.

    Lines before the first block belong to an implicit ``Host *``.
    """
    blocks: list[_Block] = [_Block(patterns=("*",))]
    visited: set[Path] = set()

    def walk(file: Path, depth: int) -> None:
        resolved = file.resolve()
        if depth > _MAX_INCLUDE_DEPTH or resolved in visited:
            return
        visited.add(resolved)
        text = _read(file)
        if text is None:
            return
        for raw in text.splitlines():
            line = _strip_comment(raw)
            if not line:
                continue
            parts = _split(line)
            if not parts:
                continue
            key, args = parts[0].lower(), parts[1:]
            if key == "include":
                for pattern in args:
                    for included in _include_paths(pattern, home):
                        walk(included, depth + 1)
            elif key == "host":
                blocks.append(_Block(patterns=tuple(args)))
            elif key == "match":
                blocks.append(_Block(patterns=(), match=True))
            elif key == "identityfile" and args:
                # The one option OpenSSH collects from every matching line.
                blocks[-1].options.setdefault(key, []).append(args[0])
            elif args:
                blocks[-1].options.setdefault(key, args)

    walk(path, 0)
    return blocks


def _aliases(blocks: list[_Block]) -> list[str]:
    seen: dict[str, None] = {}
    for block in blocks[1:]:
        for pattern in block.patterns:
            if pattern and not _is_pattern(pattern):
                seen.setdefault(pattern, None)
    return list(seen)


def resolve(blocks: list[_Block], alias: str, home: Path) -> SshConfigHost:
    """What OpenSSH would use for ``alias``: the first value per option wins."""
    options: dict[str, list[str]] = {}
    identity_files: list[str] = []
    for block in blocks:
        if not block.applies_to(alias):
            continue
        for key, value in block.options.items():
            if key == "identityfile":
                identity_files.extend(value)
            else:
                options.setdefault(key, value)
    host = (options.get("hostname") or [alias])[0].replace("%h", alias)
    port_text = (options.get("port") or ["22"])[0]
    port = int(port_text) if _PORT_RE.match(port_text) and 0 < int(port_text) < 65536 else 22
    user = (options.get("user") or [None])[0]
    files = tuple(
        path
        for path in (
            _expand_home(f.replace("%d", str(home)).replace("%h", host), home)
            for f in identity_files
        )
        if Path(path).is_file()
    )
    proxy = (options.get("proxyjump") or options.get("proxycommand") or ["none"])[0]
    return SshConfigHost(
        alias=alias,
        host=host,
        port=port,
        username=user,
        source="ssh_config",
        identity_files=files,
        needs_proxy=proxy.lower() != "none",
    )


def _known_host(raw: str) -> tuple[str, int] | None:
    bracket = re.match(r"^\[([^\]]+)\]:(\d+)$", raw)
    if bracket:
        return bracket.group(1), int(bracket.group(2))
    if raw.count(":") > 1:  # a bare IPv6 address
        return raw, 22
    return (raw.rsplit(":", 1)[0], 22) if ":" in raw else (raw, 22)


def parse_known_hosts(text: str) -> list[tuple[str, int]]:
    """The readable host entries of a ``known_hosts`` file, in file order."""
    found: dict[tuple[str, int], None] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if fields[0].startswith("@"):  # @cert-authority / @revoked
            continue
        if fields[0].startswith("|"):  # hashed: unreadable by design
            continue
        for name in fields[0].split(","):
            entry = _known_host(name.strip())
            if entry and entry[0] and not _is_pattern(entry[0]):
                found.setdefault(entry, None)
    return list(found)


def discover(home: Path | None = None) -> list[SshConfigHost]:
    """Every server this PC's ``ssh`` knows, config aliases first."""
    home = home or home_dir()
    ssh_dir = home / ".ssh"
    blocks = parse_config(ssh_dir / "config", home)
    hosts = [resolve(blocks, alias, home) for alias in _aliases(blocks)]
    covered = {(h.host.lower(), h.port) for h in hosts} | {(h.alias.lower(), h.port) for h in hosts}
    known_text = _read(ssh_dir / "known_hosts") or ""
    try:
        local_user: str | None = getpass.getuser()
    except (OSError, KeyError):
        # No login name in this environment (a service account): leave it to the form.
        local_user = None
    for host, port in parse_known_hosts(known_text):
        if (host.lower(), port) in covered or host in ("localhost", "127.0.0.1", "::1"):
            continue
        if host.lower() in _GIT_FORGES:
            continue
        covered.add((host.lower(), port))
        hosts.append(
            SshConfigHost(alias=host, host=host, port=port, username=None, source="known_hosts")
        )
    # OpenSSH logs in as the local user when the config names none.
    return [
        h if h.username or h.source == "known_hosts" else replace(h, username=local_user)
        for h in hosts
    ]


def find(alias: str, home: Path | None = None) -> SshConfigHost | None:
    """One discovered host by its alias (the add form sends the alias back)."""
    return next((h for h in discover(home) if h.alias == alias), None)
