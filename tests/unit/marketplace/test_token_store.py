"""Token store + Tokens model — persistence-critical behaviour.

The `needs_reauth` flag is the groundwork for the never-delete invariant: a
revoked token is KEPT and flagged, so a connected plugin never silently
disappears across an app close / PC restart.
"""

from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def test_needs_reauth_round_trips_through_json():
    t = Tokens(access="a", refresh="r", needs_reauth=True)
    restored = Tokens.from_json(t.to_json())
    assert restored.needs_reauth is True


def test_needs_reauth_defaults_false_for_legacy_blob():
    # A blob written before the field existed must load as needs_reauth=False.
    legacy = '{"access":"a","refresh":null,"expires_at":null,"extra":{}}'
    assert Tokens.from_json(legacy).needs_reauth is False


def test_store_persists_needs_reauth():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="a", needs_reauth=True))
    loaded = store.load("p")
    assert loaded is not None and loaded.needs_reauth is True
