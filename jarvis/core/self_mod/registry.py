"""SelfModRegistry — the schema-derived set of mutable config paths.

Wave 1.1 (Voice-First Config Control): the mutable set is no longer a
hand-maintained list. It is computed once (lazily, then cached) by walking the
``JarvisConfig`` schema — every leaf primitive field becomes voice-mutable,
minus the ``FORBIDDEN_PATTERNS`` (secrets + self-lockout). A curated override
table (``overrides.OVERRIDES``) refines risk_tier / needs_restart / description
for the paths that matter.

Plan-§AD-1 / §AP-11 is **preserved**: the set is still fixed by *code* (the
schema + overrides), computed at first use, NOT a runtime ``register()`` the LLM
could call. A new mutable field appears only by editing ``JarvisConfig`` (which
is code-reviewed). We only moved from "explicit list" to "whole schema minus
forbidden".

Public API (unchanged): ``is_forbidden``, ``is_mutable``, ``get_spec``,
``require_spec``, ``list_all``.
"""
from __future__ import annotations

from typing import ClassVar

from .errors import AllowlistViolationError, SecretAccessError
from .forbidden import FORBIDDEN_PATTERNS
from .forbidden import is_forbidden as _is_forbidden
from .schema import MutableSpec

# `FORBIDDEN_PATTERNS` is re-exported from `.forbidden` for backwards
# compatibility (callers import it from here and from the package root). The
# defense-in-depth deny layer + the self-lockout class live there so the schema
# introspector can consult it without an import cycle.

__all__ = ["FORBIDDEN_PATTERNS", "SelfModRegistry"]


class SelfModRegistry:
    """Read-only, schema-derived registry of mutable settings (Plan-§7.1)."""

    # Caches populated once on first use (NOT at import time — JarvisConfig pulls
    # in the brain→voice→self_mod chain, so deriving the set during this module's
    # import would deadlock the cycle). Deterministic: same schema → same set.
    _specs_cache: ClassVar[tuple[MutableSpec, ...] | None] = None
    _by_path_cache: ClassVar[dict[str, MutableSpec] | None] = None

    @classmethod
    def _ensure_loaded(cls) -> dict[str, MutableSpec]:
        if cls._by_path_cache is None:
            # Lazy imports break the import-time cycle (see class docstring).
            from .overrides import OVERRIDES
            from .schema_introspect import introspect_mutable_specs

            specs = introspect_mutable_specs(overrides=OVERRIDES)
            cls._specs_cache = specs
            cls._by_path_cache = {spec.path: spec for spec in specs}
        return cls._by_path_cache

    @classmethod
    def is_forbidden(cls, path: str) -> bool:
        """True if the path belongs to a protected / self-lockout section."""
        return _is_forbidden(path)

    @classmethod
    def is_mutable(cls, path: str) -> bool:
        """Hard allowlist lookup. Deny-by-default."""
        if cls.is_forbidden(path):
            return False
        return path in cls._ensure_loaded()

    @classmethod
    def get_spec(cls, path: str) -> MutableSpec | None:
        """Returns the spec for the given path — `None` if not in the mutable
        set or blocked by FORBIDDEN_PATTERNS.
        """
        if cls.is_forbidden(path):
            return None
        return cls._ensure_loaded().get(path)

    @classmethod
    def require_spec(cls, path: str) -> MutableSpec:
        """Like `get_spec`, but raises instead of returning `None`.

        - `SecretAccessError` for FORBIDDEN_PATTERNS (defense-in-depth).
        - `AllowlistViolationError` for unknown paths.
        """
        if cls.is_forbidden(path):
            raise SecretAccessError(
                f"Path '{path}' belongs to a protected section and may be "
                "neither read nor changed."
            )
        spec = cls.get_spec(path)
        if spec is None:
            raise AllowlistViolationError(
                f"Path '{path}' is not a mutable config setting "
                "(not a leaf of JarvisConfig, or excluded by the deny layer)."
            )
        return spec

    @classmethod
    def list_all(cls) -> list[MutableSpec]:
        """Returns the complete mutable set as a new list."""
        cls._ensure_loaded()
        assert cls._specs_cache is not None  # set by _ensure_loaded
        return list(cls._specs_cache)
