"""Schema introspector — derive the voice-mutable set from ``JarvisConfig``.

Voice-First Config Control, Wave 1.1. Instead of the hand-maintained allowlist,
the mutable set is computed once at import time by walking ``JarvisConfig`` and
emitting one :class:`MutableSpec` per leaf primitive field (str/int/float/bool,
including ``Optional[...]`` of those). Nested models are recursed; lists/dicts
and forbidden paths are skipped.

AD-1 is preserved: this runs in *code* at module load, not as a runtime
``register()`` the LLM could call — a new mutable field appears only by editing
``JarvisConfig`` (which is code-reviewed). The set is "whole schema minus
forbidden", still fixed and not LLM-widenable at runtime.

A curated :class:`SpecOverride` table refines ``risk_tier`` / ``needs_restart``
/ ``description`` / ``sensitive`` for known paths; every other leaf gets safe
defaults (``risk_tier="ask"``, ``needs_restart=True`` — the honest "restart to
be sure").
"""
from __future__ import annotations

import types
from collections.abc import Mapping
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from .forbidden import is_forbidden
from .schema import MutableSpec, SelfModRiskTier

# ``jarvis.core.config`` pulls in the brain → voice → self_mod chain via its
# section models (e.g. AckBrainConfig), so importing JarvisConfig at module load
# would create a cycle while ``self_mod`` is still initializing. Import it lazily
# inside the functions that need it — both run well after import time.

_PRIMITIVES: tuple[type, ...] = (bool, int, float, str)


class SpecOverride(BaseModel):
    """Curated refinement for one mutable path.

    The introspector derives ``path`` / ``pydantic_model_name`` / ``field_name``
    automatically; this supplies the human-judgement attributes the schema can't
    infer (whether a change is reversible, hot-reloadable, or value-sensitive).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_tier: SelfModRiskTier = "ask"
    needs_restart: bool = True
    sensitive: bool = False
    description: str | None = Field(default=None, min_length=1)
    # For an undeclared extra="allow" key the schema walk can't see (e.g.
    # ``ui.theme`` on UIConfig): set BOTH to force-include the path. Leave None
    # for a normal override that only refines a walked leaf.
    pydantic_model_name: str | None = Field(default=None, min_length=1)
    field_name: str | None = Field(default=None, min_length=1)


def _unwrap_optional(annotation: Any) -> Any:
    """Return the lone non-``None`` member of ``Optional[X]`` / ``X | None``."""
    if get_origin(annotation) in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _as_primitive(annotation: Any) -> bool:
    inner = _unwrap_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, _PRIMITIVES):
        return True
    # ``Literal["en", "de", "es"]`` / ``Literal[1, 2]`` — a constrained primitive
    # (string/int enum), fully settable through the dotted-path writer.
    if get_origin(inner) is Literal:
        return all(isinstance(arg, _PRIMITIVES) for arg in get_args(inner))
    return False


def _as_model(annotation: Any) -> type[BaseModel] | None:
    inner = _unwrap_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


def _humanize(path: str) -> str:
    """Fallback label for a leaf with no Pydantic ``Field(description=...)``."""
    return path.replace(".", " ").replace("_", " ").strip()


def _walk(
    model: type[BaseModel],
    prefix: str,
    overrides: Mapping[str, SpecOverride],
    out: list[MutableSpec],
) -> None:
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        nested = _as_model(field.annotation)
        if nested is not None:
            _walk(nested, path, overrides, out)
            continue
        if not _as_primitive(field.annotation):
            continue  # list/dict/tuple/other — not a scalar dotted path
        if is_forbidden(path):
            continue
        override = overrides.get(path)
        description = (override.description if override and override.description
                      else field.description) or _humanize(path)
        out.append(
            MutableSpec(
                path=path,
                pydantic_model_name=model.__name__,
                field_name=name,
                risk_tier=override.risk_tier if override else "ask",
                needs_restart=override.needs_restart if override else True,
                description=description,
                sensitive=override.sensitive if override else False,
            )
        )


def introspect_mutable_specs(
    *, overrides: Mapping[str, SpecOverride] | None = None
) -> tuple[MutableSpec, ...]:
    """Walk ``JarvisConfig`` → one :class:`MutableSpec` per leaf primitive.

    Plus: any override that carries ``pydantic_model_name`` + ``field_name`` and
    was not already produced by the walk is force-included — this is how an
    undeclared ``extra="allow"`` key (e.g. ``ui.theme``) becomes mutable.
    """
    from jarvis.core.config import JarvisConfig

    overrides = overrides or {}
    out: list[MutableSpec] = []
    _walk(JarvisConfig, "", overrides, out)

    seen = {spec.path for spec in out}
    for path, ov in overrides.items():
        if path in seen or is_forbidden(path):
            continue
        if ov.pydantic_model_name and ov.field_name:
            out.append(
                MutableSpec(
                    path=path,
                    pydantic_model_name=ov.pydantic_model_name,
                    field_name=ov.field_name,
                    risk_tier=ov.risk_tier,
                    needs_restart=ov.needs_restart,
                    description=ov.description or _humanize(path),
                    sensitive=ov.sensitive,
                )
            )
    return tuple(out)


def resolve_model_for_path(path: str) -> type[BaseModel]:
    """Navigate ``JarvisConfig`` along ``path`` to the model owning the leaf.

    More robust than ``getattr(jarvis.core.config, name)``: it follows the real
    schema, so a section model defined in a submodule (e.g.
    ``AwarenessPrivacyConfig``) and never re-exported into ``config`` still
    resolves. This is the canonical anti-drift check for an introspected spec.
    """
    from jarvis.core.config import JarvisConfig

    model: type[BaseModel] = JarvisConfig
    parts = path.split(".")
    for part in parts[:-1]:
        if part not in model.model_fields:
            raise KeyError(f"{path}: '{part}' is not a field of {model.__name__}")
        nested = _as_model(model.model_fields[part].annotation)
        if nested is None:
            raise KeyError(f"{path}: '{part}' is not a nested model")
        model = nested
    return model


# annotated-types constraint attribute -> JSON-schema-ish key.
_CONSTRAINT_ATTRS: tuple[tuple[str, str], ...] = (
    ("ge", "minimum"),
    ("gt", "exclusive_minimum"),
    ("le", "maximum"),
    ("lt", "exclusive_maximum"),
)
_TYPE_NAMES: dict[type, str] = {bool: "bool", int: "int", float: "float", str: "str"}


def describe_field(path: str) -> dict[str, Any]:
    """Describe a leaf's value type + constraints for NL value-mapping (Wave 2).

    Returns ``{"value_type": "int"|"float"|"bool"|"str"|"enum"|"unknown", ...}``
    plus, where present: ``allowed_values`` (enum), ``minimum`` / ``maximum`` /
    ``exclusive_minimum`` / ``exclusive_maximum`` (numeric Field bounds). This is
    what lets the brain turn "talk slower" into ``tts.speed = 0.8`` instead of
    guessing a type. Graceful ``{"value_type": "unknown"}`` for an unknown path
    or an undeclared ``extra="allow"`` key.
    """
    try:
        model = resolve_model_for_path(path)
    except KeyError:
        return {"value_type": "unknown"}
    field = model.model_fields.get(path.rsplit(".", 1)[-1])
    if field is None:
        return {"value_type": "unknown"}  # undeclared extra="allow" key
    annotation = _unwrap_optional(field.annotation)
    if get_origin(annotation) is Literal:
        return {"value_type": "enum", "allowed_values": list(get_args(annotation))}
    out: dict[str, Any] = {
        "value_type": _TYPE_NAMES.get(annotation, "unknown")
        if isinstance(annotation, type) else "unknown"
    }
    for meta in field.metadata:
        for attr, key in _CONSTRAINT_ATTRS:
            value = getattr(meta, attr, None)
            if value is not None:
                out[key] = value
    return out


__all__ = [
    "SpecOverride",
    "describe_field",
    "introspect_mutable_specs",
    "resolve_model_for_path",
]
