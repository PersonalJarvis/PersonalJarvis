"""Provider registry: entry_points discovery for jarvis.brain plugins.

Loads plugin classes lazily. Instantiation is performed explicitly by ``BrainManager``.
A plugin that cannot be loaded is silently skipped (missing dependency?), but
the error is recorded in the log.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from jarvis.core.protocols import Brain

PLUGIN_GROUP = "jarvis.brain"


class BrainProviderRegistry:
    """Cached entry-point discovery."""

    def __init__(self) -> None:
        self._classes: dict[str, type] = {}
        self._failed: dict[str, str] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        eps = entry_points(group=PLUGIN_GROUP)
        for ep in eps:
            try:
                cls = ep.load()
                self._classes[ep.name] = cls
            except Exception as exc:  # noqa: BLE001
                self._failed[ep.name] = f"{type(exc).__name__}: {exc}"
        self._loaded = True

    def available(self) -> list[str]:
        self._load()
        return sorted(self._classes.keys())

    def failed(self) -> dict[str, str]:
        self._load()
        return dict(self._failed)

    def instantiate(self, name: str, **kwargs: Any) -> Brain:
        """Instantiates the provider with kwargs."""
        self._load()
        if name not in self._classes:
            raise KeyError(
                f"Brain-Provider '{name}' nicht gefunden. Verfügbar: {self.available()}. "
                f"Fehlgeschlagen: {list(self._failed.keys())}"
            )
        cls = self._classes[name]
        return cls(**kwargs)

    def get_class(self, name: str) -> type:
        self._load()
        if name not in self._classes:
            raise KeyError(f"Brain-Provider '{name}' nicht gefunden.")
        return self._classes[name]
