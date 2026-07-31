"""UltraWiki connector registry + entry-point discovery.

Built-in connectors live in this package and are core code; third-party
connectors register under the ``jarvis.uw_connector`` entry-point group
and must not import ``jarvis.*`` at module level (see
:class:`jarvis.ultrawiki.types.UWConnector`).

The registry maps connector id → zero-argument factory. Consumers call
the factory ONCE PER USE so every sync run gets a fresh, stateless
connector instance. Connector modules are imported lazily inside the
factories — importing this package stays boot-cheap (AP-26).

Discovery never raises for a broken entry point: failures are recorded
(imitating ``jarvis/brain/provider_registry.py``) and readable via
:func:`discovery_failures`, so one broken third-party connector cannot
take down the whole source catalog.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from jarvis.ultrawiki.types import UWConnector

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "jarvis.uw_connector"

#: A zero-argument callable returning a fresh connector instance.
ConnectorFactory = Callable[[], UWConnector]

# Failures recorded by the most recent discover_connectors() call.
_discovery_failures: dict[str, str] = {}


def _make_obsidian_vault() -> UWConnector:
    from jarvis.ultrawiki.connectors.obsidian_vault import ObsidianVaultConnector

    return ObsidianVaultConnector()


def _make_local_folder() -> UWConnector:
    from jarvis.ultrawiki.connectors.local_folder import LocalFolderConnector

    return LocalFolderConnector()


def _make_jarvis_conversations() -> UWConnector:
    from jarvis.ultrawiki.connectors.jarvis_conversations import (
        JarvisConversationsConnector,
    )

    return JarvisConversationsConnector()


def _make_normal_wiki() -> UWConnector:
    from jarvis.ultrawiki.connectors.normal_wiki import NormalWikiConnector

    return NormalWikiConnector()


def _make_plugin_bridge() -> UWConnector:
    from jarvis.ultrawiki.connectors.plugin_bridge import PluginBridgeConnector

    return PluginBridgeConnector()


def _make_export_import() -> UWConnector:
    from jarvis.ultrawiki.connectors.export_import import ExportImportConnector

    return ExportImportConnector()


def _make_custom_source() -> UWConnector:
    from jarvis.ultrawiki.connectors.custom_source import CustomSourceConnector

    return CustomSourceConnector()


def builtin_connectors() -> dict[str, ConnectorFactory]:
    """The seven built-in connectors, keyed by connector id."""
    return {
        "obsidian-vault": _make_obsidian_vault,
        "local-folder": _make_local_folder,
        "jarvis-conversations": _make_jarvis_conversations,
        "normal-wiki": _make_normal_wiki,
        "export-import": _make_export_import,
        "custom-source": _make_custom_source,
        "plugin-bridge": _make_plugin_bridge,
    }


def _class_factory(cls: type) -> ConnectorFactory:
    def _factory() -> UWConnector:
        return cls()

    return _factory


def discover_connectors() -> dict[str, ConnectorFactory]:
    """Built-ins plus every loadable ``jarvis.uw_connector`` entry point.

    A failing entry point is recorded in :func:`discovery_failures` and
    skipped, never raised. An entry point whose name collides with a
    built-in id is ignored (the built-in wins) and recorded as a failure.
    """
    global _discovery_failures
    failures: dict[str, str] = {}
    registry = builtin_connectors()

    try:
        from importlib.metadata import entry_points

        eps = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception as exc:
        failures["<entry-points>"] = f"{type(exc).__name__}: {exc}"
        log.warning("uw_connector entry-point enumeration failed: %s", exc)
        eps = []

    for ep in eps:
        if ep.name in registry:
            failures[ep.name] = "entry point name collides with a built-in connector; ignored"
            log.warning(
                "uw_connector entry point %r collides with a built-in; ignored",
                ep.name,
            )
            continue
        try:
            loaded = ep.load()
        except Exception as exc:
            failures[ep.name] = f"{type(exc).__name__}: {exc}"
            log.warning("uw_connector entry point %r failed to load: %s", ep.name, exc)
            continue
        if isinstance(loaded, type):
            registry[ep.name] = _class_factory(loaded)
        elif callable(loaded):
            registry[ep.name] = loaded
        else:
            failures[ep.name] = "entry point is neither a class nor a factory callable"
            log.warning(
                "uw_connector entry point %r is neither a class nor a callable",
                ep.name,
            )

    _discovery_failures = failures
    return registry


def discovery_failures() -> dict[str, str]:
    """Entry-point failures recorded by the last ``discover_connectors()``."""
    return dict(_discovery_failures)


__all__ = [
    "ENTRY_POINT_GROUP",
    "ConnectorFactory",
    "builtin_connectors",
    "discover_connectors",
    "discovery_failures",
]
