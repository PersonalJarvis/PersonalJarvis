"""Bootstrap regression: handle teardown tolerates the new mirror fields."""
from __future__ import annotations

from jarvis.memory.wiki.integration import WikiIntegrationHandle


async def test_noop_handle_shutdown_with_mirror_fields():
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
    )
    await handle.shutdown()  # must not raise with all-default mirror fields


async def test_handle_runs_contact_mirror_cleanup():
    calls: list[str] = []
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
        _contact_mirror_cleanup=lambda: calls.append("cleanup"),
    )
    await handle.shutdown()
    assert calls == ["cleanup"]
