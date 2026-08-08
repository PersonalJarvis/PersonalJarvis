"""Deterministic impact classification for shell commands.

Answers, without any LLM involvement, the question a non-technical user needs
answered before a command runs: does this only READ, does it MODIFY something,
or does it DELETE/destroy something? Used by ``RunShellTool`` to escalate
destructive commands to the ``ask`` tier via the ``risk_tier_for_args`` hook
(same pattern as the gmail per-action tiers, forensic 2026-06-19), and
intended as the basis for plain-language previews in the UI/voice surface.

Design constraints:
- Pure token heuristics, no shell parser: commands are split at pipe/chain
  separators and the leading word of every segment is classified. False
  positives escalate (a read misread as modify is harmless); unknown commands
  default to MODIFY, never to READ.
- Cross-shell: understands POSIX names (``rm``, ``ls``), PowerShell
  Verb-Noun cmdlets (``Remove-Item``) and their aliases (``del``, ``gci``).
- A ``>`` / ``>>`` redirect escalates a read to MODIFY (it writes a file),
  quoted ``>`` characters cause at most an over-escalation, never an
  under-escalation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

READ = "read"
MODIFY = "modify"
DESTRUCTIVE = "destructive"

_SEVERITY = {READ: 0, MODIFY: 1, DESTRUCTIVE: 2}

# Segment separators: |, ||, &&, ;, newlines. Splitting inside quotes is
# acceptable — extra segments can only escalate severity, never lower it.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\||\n")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r">{1,2}")

# Prefix words that wrap the real command.
_WRAPPERS = frozenset({
    "sudo", "time", "nohup", "env", "command", "builtin", "exec", "xargs",
})

_DESTRUCTIVE_COMMANDS = frozenset({
    # POSIX
    "rm", "rmdir", "shred", "dd", "mkfs", "fdisk", "parted", "wipefs",
    "shutdown", "reboot", "poweroff", "halt", "truncate",
    # Windows / PowerShell (aliases included)
    "del", "erase", "rd", "format", "diskpart",
    "remove-item", "ri", "remove-itemproperty",
    "clear-content", "clc", "clear-item", "cli", "clear-disk",
    "clear-recyclebin", "stop-computer", "restart-computer",
})

_READ_COMMANDS = frozenset({
    # POSIX
    "ls", "cat", "head", "tail", "grep", "find", "pwd", "whoami", "echo",
    "printf", "date", "uname", "hostname", "df", "du", "free", "ps", "top",
    "wc", "sort", "uniq", "which", "file", "stat", "env", "id", "uptime",
    "tr", "cut", "less", "more", "basename", "dirname", "tree",
    # NOT listed on purpose: sed (-i edits in place), awk (can invoke
    # system()), tee (writes files) — they fall through to MODIFY.
    # Windows / PowerShell (aliases included)
    "dir", "type", "where.exe", "findstr", "hostname.exe", "ver", "systeminfo",
    "get-childitem", "gci", "get-content", "gc", "get-item", "gi",
    "get-process", "gps", "get-date", "get-location", "gl", "get-service",
    "get-command", "gcm", "get-help", "select-string", "sls", "select-object",
    "select", "measure-object", "measure", "test-path", "resolve-path",
    "write-output", "write-host", "format-table", "ft", "format-list", "fl",
    "out-string", "compare-object", "sort-object", "where-object", "foreach-object",
})

# PowerShell Verb-Noun classification for cmdlets not covered above. The verb
# is the part before the dash; e.g. ``Uninstall-Module`` → "uninstall".
_DESTRUCTIVE_VERBS = frozenset({"remove", "clear", "reset", "uninstall"})
_READ_VERBS = frozenset({
    "get", "select", "measure", "test", "find", "resolve", "compare", "show",
    "read", "convertto", "convertfrom", "out",
})

# Subcommand-sensitive commands: "git status" reads, "git reset" destroys.
_GIT_READ = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "describe", "blame",
    "shortlog", "tag", "ls-files", "rev-parse", "config",
})
_GIT_DESTRUCTIVE = frozenset({"reset", "clean"})


@dataclass(frozen=True, slots=True)
class CommandImpact:
    """Classification result: the worst level across all command segments."""

    level: str  # READ | MODIFY | DESTRUCTIVE
    commands: tuple[str, ...]  # the classified leading words, for previews


def _leading_word(segment: str) -> tuple[str, str]:
    """Return (command, subcommand) of a segment, normalized.

    Strips wrapper words, env assignments, path prefixes and ``.exe``
    suffixes; lowercases. Empty strings when the segment has no word.
    """
    tokens = segment.strip().split()
    while tokens and (
        tokens[0].lower() in _WRAPPERS or _ENV_ASSIGN.match(tokens[0])
    ):
        tokens = tokens[1:]
    if not tokens:
        return "", ""
    word = tokens[0].lower().replace("\\", "/").rsplit("/", 1)[-1]
    word = word.removesuffix(".exe")
    sub = tokens[1].lower() if len(tokens) > 1 else ""
    return word, sub


def _classify_segment(segment: str) -> tuple[str, str]:
    """Return (level, command_word) for one pipe/chain segment."""
    word, sub = _leading_word(segment)
    if not word:
        return READ, ""

    if word == "git":
        if sub in _GIT_DESTRUCTIVE:
            return DESTRUCTIVE, f"git {sub}"
        if sub in _GIT_READ:
            return READ, f"git {sub}"
        return MODIFY, f"git {sub}".strip()
    if word == "reg":
        return (DESTRUCTIVE if sub == "delete" else MODIFY), f"reg {sub}".strip()

    if word == "find" and "-delete" in segment.lower():
        return DESTRUCTIVE, "find -delete"

    if word in _DESTRUCTIVE_COMMANDS:
        return DESTRUCTIVE, word
    if word in _READ_COMMANDS:
        return READ, word

    # PowerShell Verb-Noun fallback ("format" alone is format.com, handled
    # above; "format-table" reaches this branch and reads).
    if "-" in word:
        verb = word.split("-", 1)[0]
        if verb in _DESTRUCTIVE_VERBS:
            return DESTRUCTIVE, word
        if verb in _READ_VERBS:
            return READ, word

    # Unknown commands default to MODIFY — never assume a stranger only reads.
    return MODIFY, word


def classify_command(command: str) -> CommandImpact:
    """Classify a full shell command string (worst segment wins)."""
    level = READ
    words: list[str] = []
    for segment in _SEGMENT_SPLIT.split(command):
        seg_level, word = _classify_segment(segment)
        if _REDIRECT.search(segment) and _SEVERITY[seg_level] < _SEVERITY[MODIFY]:
            seg_level = MODIFY
        if word:
            words.append(word)
        if _SEVERITY[seg_level] > _SEVERITY[level]:
            level = seg_level
    return CommandImpact(level=level, commands=tuple(words))
