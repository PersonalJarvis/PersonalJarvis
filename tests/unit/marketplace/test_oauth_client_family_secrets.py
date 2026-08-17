"""BINDING CONVENTION: a catalog OAuth family owns two writable secret keys.

A plugin that declares ``oauth_client_family: "<fam>"`` promises the user a
"use your own OAuth client" box in the Plugins view. That box does nothing
unless ``<fam>_oauth_client_id`` / ``<fam>_oauth_client_secret`` also exist as
wizard secrets, because ``ALLOWED_SECRET_KEYS`` — the whitelist both secret
routes enforce — is derived from exactly that list.

The failure this gate exists for shipped live on 2026-08-17: the Spotify plugin
declared the family, the dialog rendered, the user pasted a real Client ID, and
the write came back ``404 Unknown secret key: spotify_oauth_client_id``. Nothing
in the catalog, the connect helper, or the frontend could have caught it — the
two halves live in different layers and only meet at runtime.
"""
from __future__ import annotations

from jarvis.marketplace.catalog_data import load_catalog
from jarvis.ui.web.control_routes import ALLOWED_SECRET_KEYS


def _declared_families() -> set[str]:
    """Every OAuth client family the catalog declares."""
    return {
        family
        for plugin in load_catalog().plugins
        if (family := getattr(plugin, "oauth_client_family", None))
    }


def test_every_oauth_family_has_writable_client_secrets():
    missing: list[str] = []
    for family in sorted(_declared_families()):
        for suffix in ("id", "secret"):
            key = f"{family}_oauth_client_{suffix}"
            if key not in ALLOWED_SECRET_KEYS:
                missing.append(key)

    assert not missing, (
        f"OAuth client families declared in the catalog with no writable secret "
        f"key: {missing}. Add a SecretSpec for each to _WIZARD_SECRETS in "
        f"jarvis/setup/wizard.py, or the Plugins view offers a client field that "
        f"answers 404 when the user saves it."
    )


def test_spotify_family_is_wired():
    """The regression that motivated the gate, pinned explicitly."""
    assert "spotify_oauth_client_id" in ALLOWED_SECRET_KEYS
    assert "spotify_oauth_client_secret" in ALLOWED_SECRET_KEYS
