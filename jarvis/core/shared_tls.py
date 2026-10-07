"""One verified TLS context per process, shared by every httpx client.

An ``httpx`` transport built with the default ``verify=True`` calls
``ssl.create_default_context(cafile=certifi.where())``, which parses the whole
certifi bundle (~230 KB of PEM) into a fresh OpenSSL store. Measured on the
maintainer box: ~200 ms of CPU for EVERY ``httpx.Client`` / ``AsyncClient`` —
and for every SDK client built on top of one (openai, anthropic, ...). The
system store alone costs ~15 ms; the certifi parse is the whole bill.

The app builds clients in dozens of places, many of them per request and many
of them ON the backend event loop. Under memory pressure that parse is where
the loop sat: six of the 22 "Event loop STALLED for 15 s" reports in the
desktop log (2026-09-28 .. 10-02) end in ``create_ssl_context``. One shared
context makes the second and every later client free.

Sharing is safe because the context is never handed out for mutation:

* Only the default case is shared — ``verify=True`` and no client certificate.
  An explicit ``SSLContext``, ``verify=False`` or ``cert=...`` is passed
  through untouched.
* ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` (honoured by httpx when ``trust_env``)
  are part of the cache key, so changing them still takes effect.
* httpcore calls ``set_alpn_protocols`` on the context before EVERY connection.
  On a context shared across threads that is a write racing other threads'
  ``SSL_new`` (CPython releases the GIL there). Each context is therefore built
  for one ALPN list (HTTP/1.1, or HTTP/1.1 + h2) and ignores a repeated write
  of the same list, so the shared object is never written after creation.

:func:`install_when_imported` wires this into httpx without importing it: it
patches ``httpx.HTTPTransport`` / ``AsyncHTTPTransport`` right after httpx is
first imported. ``jarvis/__init__.py`` calls it, so every process that imports
the package — desktop app, background service, CLI, mission workers — gets it.
The patch checks the transport signature first (AP-28: capability, not
version); an httpx that no longer matches is left alone and simply pays the
old per-client cost.
"""

from __future__ import annotations

import functools
import os
import sys
import threading
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    import ssl
    from types import ModuleType

#: Transport parameters the patch reads; a transport lacking any is left alone.
_REQUIRED_PARAMS: Final[frozenset[str]] = frozenset({"verify", "cert", "trust_env", "http2"})
_PATCHED_MARK: Final[str] = "_jarvis_shared_tls"

_lock = threading.Lock()
_contexts: dict[tuple[bool, str | None, str | None], ssl.SSLContext] = {}


def shared_ssl_context(*, http2: bool = False, trust_env: bool = True) -> ssl.SSLContext:
    """The process-wide default client context for this ALPN shape.

    Built exactly the way httpx builds its default (same trust store, same
    ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` handling), once per key.
    """
    cafile = (os.environ.get("SSL_CERT_FILE") or None) if trust_env else None
    capath = (os.environ.get("SSL_CERT_DIR") or None) if trust_env else None
    key = (bool(http2), cafile, capath)
    ctx = _contexts.get(key)
    if ctx is not None:
        return ctx
    with _lock:
        ctx = _contexts.get(key)
        if ctx is None:
            import httpx

            ctx = httpx.create_ssl_context(verify=True, trust_env=trust_env)
            _pin_alpn(ctx, ["http/1.1", "h2"] if http2 else ["http/1.1"])
            _contexts[key] = ctx
    return ctx


def _pin_alpn(ctx: ssl.SSLContext, protocols: list[str]) -> None:
    """Set the ALPN list once and turn repeats of the same list into no-ops."""
    ctx.set_alpn_protocols(protocols)
    original = ctx.set_alpn_protocols

    def set_alpn_protocols(requested: Any) -> None:
        if list(requested) != protocols:
            # Never happens through httpcore (the key carries the http2 flag);
            # a foreign caller still gets the list it asked for.
            original(requested)

    # Instance attribute: shadows the method for this one context only.
    ctx.set_alpn_protocols = set_alpn_protocols  # type: ignore[method-assign]


def _patch_transport(cls: type) -> bool:
    """Route ``cls``'s default ``verify=True`` to the shared context."""
    init = cls.__init__
    if getattr(init, _PATCHED_MARK, False):
        return True
    import inspect  # lazy: this module is imported with the bare ``jarvis`` package

    try:
        params = inspect.signature(init).parameters
    except (TypeError, ValueError):  # an uninspectable httpx stays unpatched
        return False
    if not _REQUIRED_PARAMS <= params.keys():
        return False

    @functools.wraps(init)
    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:  # noqa: N807
        # httpx passes every transport argument by keyword; anything else is
        # a caller we do not understand, so it keeps the stock behaviour.
        if not args and kwargs.get("verify", True) is True and kwargs.get("cert") is None:
            kwargs["verify"] = shared_ssl_context(
                http2=bool(kwargs.get("http2", False)),
                trust_env=bool(kwargs.get("trust_env", True)),
            )
        init(self, *args, **kwargs)

    setattr(__init__, _PATCHED_MARK, True)
    cls.__init__ = __init__  # type: ignore[method-assign]
    return True


def install(httpx_module: ModuleType) -> bool:
    """Patch an imported httpx; ``False`` when it lacks what the patch needs."""
    if not callable(getattr(httpx_module, "create_ssl_context", None)):
        return False
    patched = False
    for name in ("HTTPTransport", "AsyncHTTPTransport"):
        cls = getattr(httpx_module, name, None)
        if isinstance(cls, type):
            patched = _patch_transport(cls) or patched
    return patched


class _InstallOnImport:
    """One-shot ``sys.meta_path`` finder: patch httpx right after it loads."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "httpx":
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass  # another thread's import already took it out; resolve normally
        from importlib.util import find_spec

        spec = find_spec(fullname)
        loader = getattr(spec, "loader", None)
        exec_module = getattr(loader, "exec_module", None)
        if exec_module is None:
            return spec

        def exec_and_install(module: ModuleType) -> None:
            exec_module(module)
            install(module)

        loader.exec_module = exec_and_install  # type: ignore[union-attr]
        return spec


def install_when_imported() -> None:
    """Patch httpx now if it is loaded, else the moment it first is."""
    module = sys.modules.get("httpx")
    if module is not None:
        install(module)
        return
    if not any(isinstance(finder, _InstallOnImport) for finder in sys.meta_path):
        sys.meta_path.insert(0, _InstallOnImport())


__all__ = ["install", "install_when_imported", "shared_ssl_context"]
