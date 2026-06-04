"""Bridge layer between the CLI catalog and the safety risk-tier evaluator.

``make_cli_patterns_fn()`` returns an ``ExtraPatternsFn`` that flattens all
per-CLI whitelist/blacklist patterns from the ``CliCatalog``. Each pattern is
prefixed with the tool name (``cli_gcloud * delete *`` etc.) so that the
fnmatch regex matches against ``"<tool_name> <args>"``.

Prefers ``shared.get_active_registry()`` (published by the UI server) —
which also knows about custom CLIs at runtime. Fallback: direct catalog scan.

Errors are swallowed and return ``([], [])`` — the safety gate must never
hang, even if the catalog is broken.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def make_cli_patterns_fn() -> Callable[[], "tuple[list[str], list[str]]"]:
    """Factory for an ``ExtraPatternsFn`` (compatible with RiskTierEvaluator).

    The returned function accepts no arguments and returns a
    ``(whitelist, blacklist)`` tuple.
    """

    def _fn() -> tuple[list[str], list[str]]:
        try:
            # Preferred: running registry with custom CLIs
            from jarvis.clis.shared import get_active_registry

            registry = get_active_registry()
            if registry is not None and hasattr(registry, "risk_patterns"):
                return registry.risk_patterns()

            # Fallback: scan catalog directly
            from jarvis.clis.catalog import CliCatalog
            from jarvis.clis.tool import TOOL_NAME_PREFIX

            catalog = CliCatalog()
            whitelist: list[str] = []
            blacklist: list[str] = []
            for spec in catalog.all().values():
                prefix = f"{TOOL_NAME_PREFIX}{spec.name} "
                whitelist.extend(f"{prefix}{p}" for p in spec.risk.whitelist_patterns)
                blacklist.extend(f"{prefix}{p}" for p in spec.risk.blacklist_patterns)
            return whitelist, blacklist
        except Exception as exc:  # noqa: BLE001
            log.debug("cli_patterns_fn failed: %s", exc)
            return [], []

    return _fn


__all__ = ["make_cli_patterns_fn"]
