"""Family metadata cannot delegate another provider's confidential client."""

from types import SimpleNamespace

import httpx
import pytest

from jarvis.marketplace import catalog_data
from jarvis.marketplace.auth.oauth_pkce_loopback import _PendingPkceFlow
from jarvis.marketplace.connect_helpers import build_handler_from_catalog, oauth_client_family
from jarvis.marketplace.publisher_clients import resolve_publisher_client


@pytest.mark.parametrize("source", ["community", "local", "seed"])
@pytest.mark.parametrize("auth_method", ["client_secret_post", "client_secret_basic"])
@pytest.mark.parametrize("provisioning", ["own", "publisher"])
async def test_untrusted_token_endpoint_never_receives_family_secret(
    monkeypatch, source, auth_method, provisioning
):
    seed = catalog_data.load_seed_catalog()
    original = seed.by_id("gmail")
    plugin_id = "gmail" if source == "seed" else "community-mail"
    auth = original.auth.model_copy(
        update={
            "authorization_url": "https://community.invalid/authorize",
            "token_url": "https://community.invalid/token",
            "client_id": "community-client",
            "client_secret": "community-own-secret",
            "client_auth_method": auth_method,
        }
    )
    spec = original.model_copy(update={"id": plugin_id, "source": source, "auth": auth})
    monkeypatch.setattr(
        catalog_data, "load_catalog", lambda: SimpleNamespace(by_id=lambda pid: spec)
    )
    secret_reads = []

    def secret(key, env=None):
        secret_reads.append(key)
        prefix = "google" if provisioning == "own" else "publisher_google"
        return {
            f"{prefix}_oauth_client_id": "unrelated-client",
            f"{prefix}_oauth_client_secret": "unrelated-secret",
        }.get(key)

    monkeypatch.setattr("jarvis.core.config.get_secret", secret)
    assert oauth_client_family(plugin_id) is None
    assert resolve_publisher_client(plugin_id, auth.client_id, auth.client_secret) == (
        "community-client",
        "community-own-secret",
        "catalog",
    )
    handler = build_handler_from_catalog(plugin_id)
    requests = []

    def capture(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "synthetic-access"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(capture), **kwargs),
    )
    pending = _PendingPkceFlow(
        handler._config, None, "synthetic-verifier", "http://127.0.0.1/callback"
    )
    result = await handler._exchange(pending, code="synthetic-code")
    assert result.access == "synthetic-access"
    assert len(requests) == 1
    assert "unrelated" not in requests[0].content.decode()
    assert handler._config.client_secret == "community-own-secret"  # noqa: S105 - synthetic fixture
    assert secret_reads == []


@pytest.mark.parametrize("plugin_id", ["gmail", "google_drive", "google_calendar"])
@pytest.mark.parametrize("provisioning", ["own", "publisher"])
def test_trusted_siblings_keep_shared_clients(monkeypatch, plugin_id, provisioning):
    seed = catalog_data.load_seed_catalog()
    monkeypatch.setattr(catalog_data, "load_catalog", lambda: seed)
    prefix = "google" if provisioning == "own" else "publisher_google"
    secrets = {
        f"{prefix}_oauth_client_id": "trusted-client",
        f"{prefix}_oauth_client_secret": "trusted-secret",
    }
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda key, env=None: secrets.get(key))
    assert oauth_client_family(plugin_id) == "google"
    assert resolve_publisher_client(plugin_id, "placeholder", None) == (
        "trusted-client",
        "trusted-secret",
        provisioning,
    )


def test_unreadable_seed_disables_secret_sharing(monkeypatch):
    def broken():
        raise ValueError("bad seed")

    monkeypatch.setattr(catalog_data, "load_seed_catalog", broken)
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda *args: pytest.fail("secret read on untrusted catalog"),
    )
    assert oauth_client_family("gmail") is None
