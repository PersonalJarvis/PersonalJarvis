"""Explicit OS permission fixtures for synthetic speech consumers."""

import pytest

from tests.fakes.fake_permission_service import FakePermissionService


@pytest.fixture
def granted_microphone(monkeypatch):
    """A fake capture device needs a fake grant, independent of host TCC.

    Opt in only for consumer tests using synthetic audio. Permission boundary
    tests still inject their own scripted gate or exercise the real service.
    """
    gate = FakePermissionService()
    monkeypatch.setattr(
        "jarvis.platform.permission_service.get_permission_service", lambda: gate,
    )
    return gate
