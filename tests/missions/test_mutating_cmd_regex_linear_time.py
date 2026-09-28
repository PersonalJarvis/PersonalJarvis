"""The mutating git/gh command detector never backtracks exponentially.

CodeQL py/redos: an option such as ``-c -x`` or ``--git-dir=x`` had two
readings, so a long option list that ends without a mutating subcommand
doubled the work per option. Each token now has one parse.
"""

from __future__ import annotations

import time

import pytest

from jarvis.missions.stream_evidence import _MUTATING_CMD_RE


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push origin main", True),
        ("git -C /tmp/repo commit -m x", True),
        ("git -c user.name=x push", True),
        ("git -c -x push", True),
        ("git -c - push", True),
        ("git --no-pager commit", True),
        ("git --git-dir=/r/.git push", True),
        ("git --git-dir /r/.git --work-tree /r tag v1", True),
        ("git --git-dir -x push", True),
        ("git log --grep=push", False),
        ("git status", False),
        ("echo gitpush", False),
        ("gh pr create --fill", True),
        ("gh pr list", False),
    ],
)
def test_mutating_cmd_normal(command: str, expected: bool) -> None:
    assert bool(_MUTATING_CMD_RE.search(command)) is expected


@pytest.mark.parametrize(
    "tail",
    [
        " -c -!" * 40,
        " -c -! " * 40,
        " -! -" * 40,
        " --x" * 40,
        " --git-dir=x" * 40,
        " --git-dir -" * 40,
        " -c x" * 5000,
    ],
    ids=[
        "value-is-flag",
        "value-is-flag-spaced",
        "flag-pairs",
        "double-dash",
        "git-dir-eq",
        "git-dir-lone-dash",
        "many-pairs",
    ],
)
def test_mutating_cmd_pathological(tail: str) -> None:
    start = time.perf_counter()
    assert _MUTATING_CMD_RE.search("git" + tail + " status") is None
    assert time.perf_counter() - start < 1.0
