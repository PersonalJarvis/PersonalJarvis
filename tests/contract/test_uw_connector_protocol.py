"""Contract tests for all UWConnector implementations.

Parametrized over :func:`discover_connectors` — the five built-ins are
always present, and every installed ``jarvis.uw_connector`` entry-point
plugin joins the same matrix. Third-party checks skip gracefully when no
entry-point plugins are installed (mirroring
``tests/contract/test_channel_adapter_contract.py``).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from jarvis.ultrawiki.connectors import builtin_connectors, discover_connectors
from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    UWConnector,
)

_REGISTRY = discover_connectors()
_PARAMS = sorted(_REGISTRY.items())
_BUILTIN_NAMES = frozenset(builtin_connectors())
_ENTRY_POINT_PARAMS = [
    (name, factory) for name, factory in _PARAMS if name not in _BUILTIN_NAMES
] or [("skip", None)]

# Matches a MODULE-LEVEL (unindented) jarvis import; lazy in-function
# imports are the sanctioned pattern and stay allowed.
_MODULE_LEVEL_JARVIS_IMPORT = re.compile(
    r"^(?:import jarvis\b|from jarvis[.\s])", re.MULTILINE
)


def _ctx() -> ConnectorContext:
    return ConnectorContext(source_id="contract-test", config={})


async def _close_quietly(gen) -> None:
    aclose = getattr(gen, "aclose", None)
    if aclose is not None:
        await aclose()


@pytest.mark.parametrize("name,factory", _PARAMS)
def test_connector_satisfies_the_frozen_protocol(name, factory):
    instance = factory()
    assert isinstance(instance, UWConnector), (
        f"{name} does not satisfy the UWConnector protocol"
    )
    assert isinstance(instance.id, str) and instance.id
    assert isinstance(instance.label, str) and instance.label
    assert isinstance(instance.auth, AuthKind)
    assert isinstance(instance.capabilities, ConnectorCapabilities)
    assert isinstance(instance.capabilities.backfill, bool)
    assert isinstance(instance.capabilities.incremental, IncrementalMode)
    assert isinstance(instance.capabilities.deletes, bool)


@pytest.mark.parametrize("name,factory", _PARAMS)
def test_factory_returns_a_fresh_instance_per_use(name, factory):
    assert factory() is not factory()


@pytest.mark.parametrize("name,factory", _PARAMS)
async def test_backfill_and_incremental_are_async_generators(name, factory):
    instance = factory()
    ctx = _ctx()

    backfill_gen = instance.backfill(ctx)
    assert hasattr(backfill_gen, "__aiter__") and hasattr(backfill_gen, "__anext__"), (
        f"{name}.backfill(ctx) must return an async iterator"
    )
    await _close_quietly(backfill_gen)

    incremental_gen = instance.incremental(ctx, None)
    assert hasattr(incremental_gen, "__aiter__") and hasattr(
        incremental_gen, "__anext__"
    ), f"{name}.incremental(ctx, cursor) must return an async iterator"
    await _close_quietly(incremental_gen)


@pytest.mark.parametrize("name,factory", _PARAMS)
def test_backfill_accepts_the_optional_checkpoint(name, factory):
    signature = inspect.signature(factory().backfill)
    parameters = list(signature.parameters.values())
    assert len(parameters) >= 2, (
        f"{name}.backfill must accept (ctx, checkpoint=None)"
    )


@pytest.mark.parametrize("name,factory", _ENTRY_POINT_PARAMS)
def test_entry_point_connector_has_no_module_level_jarvis_import(name, factory):
    """Third-party connectors stay decoupled via structural typing."""
    if factory is None:
        pytest.skip("no third-party jarvis.uw_connector entry points installed")
    module = inspect.getmodule(type(factory()))
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        pytest.skip(f"source file for {name} is unavailable")
    source = Path(module_file).read_text(encoding="utf-8", errors="replace")
    assert not _MODULE_LEVEL_JARVIS_IMPORT.search(source), (
        f"entry-point connector {name} must not import jarvis.* at module level"
    )
