"""Shared agent memory refuses a write when the secret detector cannot run.

Every agent reads the shared memory, so a broken detector must not let a
credential through. The learning guard already fails closed; this pins the
society memory to the same rule.
"""
from __future__ import annotations

import pytest

from jarvis.memory.wiki import secret_guard
from jarvis.society import memory


def test_detector_failure_counts_as_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(text: str) -> bool:
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(secret_guard, "contains_secret", broken)

    assert memory._contains_secret("an ordinary sentence") is True


def test_working_detector_still_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secret_guard, "contains_secret", lambda text: False)

    assert memory._contains_secret("an ordinary sentence") is False
