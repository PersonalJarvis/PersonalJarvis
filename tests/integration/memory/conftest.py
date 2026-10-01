"""Shared fixtures for the memory integration suites."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def background_billing(tmp_path_factory, monkeypatch):  # noqa: ANN001, ANN201
    """Key-only install by default: no subscription, nothing spent today.

    Keeps the background billing policy and the wiki's runaway guard away from
    the developer's real subscription marker and daily call counter.
    """
    from tests.fakes.fake_background_billing import (
        isolate_background_billing,
        reset_background_billing,
    )

    billing = isolate_background_billing(
        monkeypatch, tmp_path_factory.mktemp("background-billing")
    )
    yield billing
    reset_background_billing()
