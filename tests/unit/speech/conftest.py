"""Shared fixtures for the speech suite.

Explicit OS permission fixtures for synthetic speech consumers, and the one
autouse guarantee that a realtime transport is picked as on a keyless host.
"""

import pytest

from tests.fakes.fake_permission_service import FakePermissionService


@pytest.fixture(autouse=True)
def _realtime_transport_sees_no_host_credentials(monkeypatch):
    """Pick the realtime transport as a keyless host (and CI) does.

    Without a pinned provider, ``realtime_browser_audio`` follows the first
    credential-ready provider. On a developer machine with a real Gemini key
    every desktop-path test would then hand its call to the browser, while CI
    never does. A test that needs a ready provider patches ``get_secret_any``
    in its own body, which runs after this fixture and wins.
    """
    monkeypatch.setattr(
        "jarvis.realtime.factory.get_secret_any", lambda _candidates: None
    )


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
