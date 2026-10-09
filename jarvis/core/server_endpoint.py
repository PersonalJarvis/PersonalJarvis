"""Validate a client connection without putting credentials into its URL."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def server_endpoint(value: str) -> str:
    """Accept TLS servers or local HTTP, with no embedded login or query."""
    value = value.strip()
    if any(char.isspace() for char in value):
        raise ValueError("The server address must not contain whitespace.")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use a server origin such as https://jarvis.example.com without a key.")
    try:
        port = parsed.port
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        # A DNS host is valid; an invalid port still needs to fail validation.
        port = parsed.port
        local = parsed.hostname == "localhost"
    if parsed.scheme == "http" and not local:
        raise ValueError("Remote servers require HTTPS. An SSH tunnel may use local HTTP.")
    if port is not None and port < 1:
        raise ValueError("The server port must be between 1 and 65535.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
