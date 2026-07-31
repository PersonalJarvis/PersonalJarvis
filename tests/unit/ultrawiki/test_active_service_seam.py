"""The active-service seam the brain's memory surfaces resolve through.

``wiki-recall`` and the system-prompt context injector live below the web
layer and cannot read ``app.state``. These tests pin the contract they depend
on: a registered service is found, a cleared one is gone, and a service that
cannot answer (mode off, store not open) is reported as absent rather than
handed out — because both callers treat "present" as "this memory answers now".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.ultrawiki import service as service_mod
from jarvis.ultrawiki.service import (
    UltraWikiService,
    active_search_service,
    get_active_service,
    set_active_service,
)


class _FakeService:
    """Minimal stand-in exposing exactly what the readiness check reads."""

    def __init__(self, *, enabled: bool = True, started: bool = True) -> None:
        self._enabled = enabled
        self._store = object() if started else None

    def _uw_enabled(self) -> bool:
        return self._enabled


@pytest.fixture(autouse=True)
def _clean_seam(monkeypatch: pytest.MonkeyPatch):
    """Isolate the process-global seam and the app-state fallback."""
    from jarvis.core import runtime_refs

    monkeypatch.setattr(runtime_refs, "get_web_app", lambda: None)
    set_active_service(None)
    yield
    set_active_service(None)


def test_register_and_clear_round_trip() -> None:
    service = _FakeService()
    set_active_service(service)
    assert get_active_service() is service

    set_active_service(None)
    assert get_active_service() is None


def test_unregistered_seam_falls_back_to_app_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime that never called the setter still resolves its service."""
    from jarvis.core import runtime_refs

    sentinel = object()
    monkeypatch.setattr(
        runtime_refs,
        "get_web_app",
        lambda: SimpleNamespace(state=SimpleNamespace(ultrawiki=sentinel)),
    )
    assert get_active_service() is sentinel


def test_ready_service_is_handed_out() -> None:
    service = _FakeService(enabled=True, started=True)
    set_active_service(service)
    assert active_search_service() is service


def test_mode_off_reads_as_no_service() -> None:
    set_active_service(_FakeService(enabled=False, started=True))
    assert active_search_service() is None


def test_unopened_store_reads_as_no_service() -> None:
    set_active_service(_FakeService(enabled=True, started=False))
    assert active_search_service() is None


def test_readiness_never_raises_on_a_broken_handle() -> None:
    class _Broken:
        def _uw_enabled(self) -> bool:
            raise RuntimeError("handle is toast")

    set_active_service(_Broken())
    assert active_search_service() is None


def test_a_real_service_with_the_mode_off_is_not_handed_out() -> None:
    """Same answer through the real class, not just the fake."""
    cfg = SimpleNamespace(ultrawiki=SimpleNamespace(enabled=False))
    set_active_service(UltraWikiService(cfg))
    assert active_search_service() is None


# ---------------------------------------------------------------------------
# Server wiring
# ---------------------------------------------------------------------------


async def test_server_init_registers_the_constructed_service() -> None:
    """The WebServer's UltraWiki init must publish its instance to the seam."""
    from jarvis.ui.web.server import WebServer

    stub = SimpleNamespace(
        cfg=SimpleNamespace(ultrawiki=SimpleNamespace(enabled=False)),
        bus=None,
        app=SimpleNamespace(state=SimpleNamespace()),
        _ultrawiki_start_task=None,
    )
    await WebServer._init_ultrawiki(stub)

    assert isinstance(stub.app.state.ultrawiki, UltraWikiService)
    assert get_active_service() is stub.app.state.ultrawiki


def test_server_stop_clears_the_seam() -> None:
    """Teardown must retract the handle before the store closes.

    Asserted on the source because ``stop()`` tears down the whole server and
    cannot be driven from a stub — but the seam clear is exactly the line that
    would be silently dropped in a future refactor, leaving the brain holding a
    service whose store is closed.
    """
    import inspect

    from jarvis.ui.web.server import WebServer

    source = inspect.getsource(WebServer.stop)
    assert "set_active_service(None)" in source
    assert source.index("set_active_service(None)") < source.index(
        "ultrawiki_service.shutdown()"
    ), "the seam must be cleared BEFORE the service shuts down"


def test_seam_is_exported() -> None:
    for name in ("set_active_service", "get_active_service", "active_search_service"):
        assert name in service_mod.__all__
