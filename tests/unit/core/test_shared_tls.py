"""The process-wide TLS context httpx transports share (jarvis.core.shared_tls)."""

from __future__ import annotations

import ssl
import sys

import httpx
import pytest

from jarvis.core import shared_tls


class _FakeTransport:
    """Mirrors the keyword signature of httpx's transports."""

    def __init__(
        self,
        verify: object = True,
        cert: object = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
    ) -> None:
        self.verify = verify


@pytest.fixture
def fake_transport() -> type[_FakeTransport]:
    cls = type("PatchedFake", (_FakeTransport,), {})
    assert shared_tls._patch_transport(cls)
    return cls


@pytest.fixture(autouse=True)
def _no_cert_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def test_default_verify_gets_one_shared_context(fake_transport) -> None:
    first = fake_transport(verify=True, http2=False)
    second = fake_transport(http2=False)
    assert isinstance(first.verify, ssl.SSLContext)
    assert first.verify is second.verify
    assert first.verify.verify_mode == ssl.CERT_REQUIRED
    assert first.verify.check_hostname


def test_http2_clients_get_their_own_context(fake_transport) -> None:
    h1 = fake_transport(http2=False)
    h2 = fake_transport(http2=True)
    assert h1.verify is not h2.verify


def test_explicit_choices_pass_through(fake_transport) -> None:
    custom = ssl.create_default_context()
    assert fake_transport(verify=False).verify is False
    assert fake_transport(verify=custom).verify is custom
    assert fake_transport(verify=True, cert="client.pem").verify is True
    assert fake_transport(True).verify is True  # positional caller: stock path


def test_cert_env_vars_are_part_of_the_key(fake_transport, monkeypatch) -> None:
    import certifi

    plain = fake_transport().verify
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    from_env = fake_transport().verify
    ignored_env = fake_transport(trust_env=False).verify
    assert from_env is not plain
    assert ignored_env is plain


def test_repeated_alpn_write_never_touches_the_shared_context(monkeypatch) -> None:
    ctx = ssl.create_default_context()
    writes: list[list[str]] = []
    original = ctx.set_alpn_protocols
    ctx.set_alpn_protocols = lambda p: (writes.append(list(p)), original(p))  # type: ignore[method-assign]
    shared_tls._pin_alpn(ctx, ["http/1.1"])
    ctx.set_alpn_protocols(["http/1.1"])
    ctx.set_alpn_protocols(["http/1.1"])
    assert writes == [["http/1.1"]]
    ctx.set_alpn_protocols(["http/1.1", "h2"])  # a foreign shape still applies
    assert writes[-1] == ["http/1.1", "h2"]


def test_patch_is_idempotent_and_capability_gated() -> None:
    cls = type("Twice", (_FakeTransport,), {})
    assert shared_tls._patch_transport(cls)
    patched_init = cls.__init__
    assert shared_tls._patch_transport(cls)
    assert cls.__init__ is patched_init

    class _Unknown:
        def __init__(self, verify: object = True) -> None:
            self.verify = verify

    stock_init = _Unknown.__init__
    assert not shared_tls._patch_transport(_Unknown)
    assert _Unknown.__init__ is stock_init


def test_install_skips_an_httpx_without_create_ssl_context() -> None:
    module = type(sys)("fake_httpx")
    module.HTTPTransport = type("HTTPTransport", (_FakeTransport,), {})
    stock_init = module.HTTPTransport.__init__
    assert not shared_tls.install(module)
    assert module.HTTPTransport.__init__ is stock_init


def test_real_httpx_is_patched_once_jarvis_is_imported() -> None:
    import jarvis  # noqa: F401 — the import installs the patch

    assert getattr(httpx.HTTPTransport.__init__, shared_tls._PATCHED_MARK, False)
    assert getattr(httpx.AsyncHTTPTransport.__init__, shared_tls._PATCHED_MARK, False)
    with httpx.Client() as a, httpx.Client() as b:
        # Building clients must not fail and must not re-parse the CA bundle;
        # the shared context is what the default path now resolves to.
        assert a is not b
    assert shared_tls.shared_ssl_context() is shared_tls.shared_ssl_context()


def test_import_hook_patches_httpx_on_first_import(monkeypatch) -> None:
    finder = shared_tls._InstallOnImport()
    installed: list[object] = []
    monkeypatch.setattr(shared_tls, "install", installed.append)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    monkeypatch.delitem(sys.modules, "httpx")

    spec = finder.find_spec("httpx")
    assert finder not in sys.meta_path  # one-shot
    from importlib.util import module_from_spec

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert installed == [module]
    assert finder.find_spec("not_httpx") is None
