"""The contact e-mail check stays linear on long, hostile input.

CodeQL py/polynomial-redos: ``[^@\\s]+\\.[^@\\s]+`` could split a long dotted
domain at every dot before failing, and vCard files are untrusted input.
"""

from __future__ import annotations

import time

import pytest

from jarvis.contacts.store import _EMAIL_RE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ada@example.com", True),
        ("a.b+c@sub.example.co.uk", True),
        ("a@.b.c", True),
        ("a@b..c", True),
        ("a@.bc", False),
        ("a@bc.", False),
        ("a@bc", False),
        ("a b@c.d", False),
        ("a@b@c.d", False),
        ("@b.c", False),
    ],
)
def test_email_normal(value: str, expected: bool) -> None:
    assert bool(_EMAIL_RE.match(value)) is expected


def test_email_pathological() -> None:
    value = "!@!." + "!." * 25_000 + "@"
    start = time.perf_counter()
    assert _EMAIL_RE.match(value) is None
    assert time.perf_counter() - start < 1.0
