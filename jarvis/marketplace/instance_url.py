"""Normalize the base address of a self-hosted service.

Home Assistant, Jellyfin, Nextcloud, Paperless and Immich have no fixed public
endpoint — the address is the user's own. People type it the way they see it in
their browser, which means a trailing slash, a path they happened to be on, a
missing scheme, or a copied URL with a query string. All of those are the same
server, and none of them should fail a connection.

Deliberately does NOT resolve or reach anything: it only shapes the string. The
connect flow finds out whether the address is real by actually calling it.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# A plain hostname without a scheme is far more likely to be a LAN box reached
# over http than a public https host, but guessing wrong costs a confusing
# failure. https is the safer default: a user running plain http can type it,
# whereas silently downgrading to http would send their long-lived token in the
# clear.
_DEFAULT_SCHEME = "https"


class InstanceUrlError(ValueError):
    """The entered address cannot be a service base URL."""


def normalize_instance_url(raw: str) -> str:
    """Return ``scheme://host[:port]`` with no trailing slash.

    Raises :class:`InstanceUrlError` when the input cannot be an address at all,
    so the caller can say something useful instead of failing later with a
    confusing HTTP error.
    """
    value = (raw or "").strip()
    if not value:
        raise InstanceUrlError("no address given")

    # `urlsplit` reads a scheme-less "homeassistant.local:8123" as scheme
    # "homeassistant.local" — the port looks like a scheme to it. Add the
    # scheme first so the whole string is parsed as an authority.
    if "//" not in value:
        value = f"{_DEFAULT_SCHEME}://{value}"

    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise InstanceUrlError(
            f"address must start with http:// or https:// (got {parts.scheme!r})"
        )
    if not parts.hostname:
        raise InstanceUrlError("address is missing a host name")

    # Drop whatever page the user happened to copy from, plus any query or
    # fragment: what we need is the server root.
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
