from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.ui.web import lan_access, surface_security


def _cfg(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(ui=SimpleNamespace(lan_access=enabled, lan_port=47843))


def test_certificate_names_the_lan_ip_and_is_reused(tmp_path: Path) -> None:
    from cryptography import x509

    cert, key = lan_access.ensure_certificate("192.168.1.20", tmp_path)
    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    san = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert [str(ip) for ip in san.value.get_values_for_type(x509.IPAddress)] == ["192.168.1.20"]
    first = cert.read_bytes()
    lan_access.ensure_certificate("192.168.1.20", tmp_path)
    assert cert.read_bytes() == first  # same IP -> same certificate
    lan_access.ensure_certificate("192.168.1.21", tmp_path)
    assert cert.read_bytes() != first  # new IP -> reissued
    assert key.is_file()


def test_off_means_no_origin_and_no_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_access, "lan_ip", lambda: "192.168.1.20")
    assert lan_access.lan_origin(_cfg(False)) is None
    assert lan_access.mint_pairing_url(_cfg(False)) is None


def test_pairing_token_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_access, "lan_ip", lambda: "192.168.1.20")
    url = lan_access.mint_pairing_url(_cfg(True))
    assert url is not None and url.startswith("https://192.168.1.20:47843/#pair=")
    token = url.split("#pair=", 1)[1]
    assert surface_security._consume_bootstrap_token(token)
    assert not surface_security._consume_bootstrap_token(token)


def test_public_addresses_are_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Sock:
        def __init__(self, *a: object) -> None: ...
        def __enter__(self) -> _Sock:
            return self
        def __exit__(self, *a: object) -> None: ...
        def connect(self, _addr: object) -> None: ...
        def getsockname(self) -> tuple[str, int]:
            return ("8.8.8.8", 1)

    monkeypatch.setattr(lan_access.socket, "socket", _Sock)
    assert lan_access.lan_ip() is None
