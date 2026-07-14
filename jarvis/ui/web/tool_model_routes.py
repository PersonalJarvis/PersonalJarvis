"""Capability-aware Tool Model configuration and status routes."""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.brain.app_control import is_credential_present
from jarvis.brain.model_catalog import model_capabilities
from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.core.config import BrainProviderConfig, BrainTierConfig
from jarvis.core.events import SecretConfigured

from .provider_spec import get_spec

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tool-model", tags=["tool-model"])


class ToolModelSelection(BaseModel):
    """Canonical Tool Model selection; ``auto`` chooses any usable family."""

    provider: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    persist: bool = True


class ToolModelStatus(BaseModel):
    configured_provider: str
    configured_model: str | None = None
    effective_provider: str | None = None
    effective_model: str | None = None
    state: Literal["ready", "fallback", "blocked"]
    reason: str
    source: Literal["tool_model", "computer_use", "auto"]
    tools: bool | None = None
    vision: bool | None = None
    persisted: bool = False
    restart_required: bool = False


def _config(request: Request) -> Any:
    cfg = getattr(request.app.state, "config", None) or getattr(
        request.app.state, "cfg", None
    )
    if cfg is None:
        raise HTTPException(status_code=503, detail="Configuration is unavailable.")
    return cfg


def _selection(cfg: Any) -> tuple[str, str | None, str]:
    brain = cfg.brain
    fields_set = getattr(brain, "model_fields_set", set())
    canonical = getattr(brain, "tool_model", None)
    legacy = getattr(brain, "computer_use", None)
    if canonical is not None:
        provider = (getattr(canonical, "provider", None) or "auto").strip()
        source = "tool_model" if "tool_model" in fields_set else "computer_use"
    elif legacy is not None:
        provider = (getattr(legacy, "provider", None) or "auto").strip()
        source = "computer_use"
    else:
        provider, source = "auto", "auto"
    pc = brain.providers.get(provider) if provider != "auto" else None
    model = None
    if pc is not None:
        model = (
            getattr(pc, "tool_model", None)
            or getattr(pc, "cu_model", None)
            or getattr(pc, "model", None)
        )
    return provider or "auto", model, source


def _fallback_status(request: Request) -> dict[str, Any]:
    """Resolve a safe status when no live BrainManager is attached."""
    cfg = _config(request)
    provider, model, source = _selection(cfg)
    if provider == "auto":
        return {
            "configured_provider": "auto",
            "configured_model": None,
            "effective_provider": None,
            "effective_model": None,
            "state": "blocked",
            "reason": "brain_manager_unavailable",
            "source": source,
            "tools": None,
            "vision": None,
        }
    verdict = _static_candidate_status(provider, model)
    return {
        "configured_provider": provider,
        "configured_model": model,
        "effective_provider": provider if verdict["ready"] else None,
        "effective_model": model if verdict["ready"] else None,
        "state": "ready" if verdict["ready"] else "blocked",
        "reason": "configured_selection" if verdict["ready"] else verdict["reason"],
        "source": source,
        "tools": verdict["tools"],
        "vision": verdict["vision"],
    }


def _static_candidate_status(provider: str, model: str | None) -> dict[str, Any]:
    """Fail closed on known blockers without starting a provider session."""
    spec = get_spec(provider)
    if spec is None:
        return {"ready": False, "reason": "unknown_provider", "tools": None, "vision": None}
    if spec.tier != "brain" or not spec.brain_switchable:
        return {"ready": False, "reason": "provider_not_switchable", "tools": None, "vision": None}
    if not is_credential_present(spec):
        return {"ready": False, "reason": "missing_credential", "tools": None, "vision": None}
    if provider not in set(BrainProviderRegistry().available()):
        return {"ready": False, "reason": "provider_unavailable", "tools": None, "vision": None}

    capabilities = model_capabilities(provider, model or "")
    tools = capabilities["tools"]
    vision = capabilities["vision"]
    if tools is False:
        return {"ready": False, "reason": "tools_unsupported", "tools": False, "vision": vision}
    try:
        provider_class = BrainProviderRegistry().get_class(provider)
        class_tools = getattr(provider_class, "supports_tools", None)
    except Exception:  # noqa: BLE001 -- registry availability was checked above
        class_tools = None
    if class_tools is False:
        return {"ready": False, "reason": "tools_unsupported", "tools": False, "vision": vision}
    return {"ready": True, "reason": "ready", "tools": tools, "vision": vision}


def _status(request: Request) -> dict[str, Any]:
    brain = getattr(request.app.state, "brain", None)
    resolver = getattr(brain, "resolve_tool_model", None)
    if callable(resolver):
        try:
            return dict(resolver())
        except Exception as exc:  # noqa: BLE001 -- health routes never crash the app
            log.warning("Tool Model status resolution failed: %s", exc)
    return _fallback_status(request)


async def _publish_configured(request: Request) -> None:
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        brain = getattr(request.app.state, "brain", None)
        bus = getattr(brain, "_bus", None) or getattr(brain, "bus", None)
    if bus is not None:
        try:
            await bus.publish(
                SecretConfigured(key="brain.tool_model.provider", action="set")
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not publish Tool Model config event: %s", exc)


@router.get("/status", summary="Show the effective Tool Model")
async def get_tool_model_status(request: Request) -> ToolModelStatus:
    """Return configured and effective capability-aware Tool Model state."""
    return ToolModelStatus(**_status(request))


@router.put("", summary="Select the global Tool Model")
async def set_tool_model(
    body: ToolModelSelection, request: Request
) -> ToolModelStatus:
    """Select a tool-capable provider/model or enable automatic selection."""
    provider = body.provider.strip()
    model = body.model.strip() if body.model is not None else None
    brain = getattr(request.app.state, "brain", None)
    if not provider:
        raise HTTPException(status_code=400, detail="Tool Model provider is required.")
    if provider == "auto" and model not in (None, ""):
        raise HTTPException(
            status_code=400,
            detail="Automatic Tool Model selection cannot pin a provider model.",
        )

    if provider != "auto":
        spec = get_spec(provider)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
        if spec.tier != "brain" or not spec.brain_switchable:
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider}' cannot be used as the Tool Model.",
            )
        if not is_credential_present(spec):
            raise HTTPException(
                status_code=409,
                detail=f"{spec.label} has no saved credential. Save one first.",
            )

        # An explicit in-app selection is also the recovery signal for a
        # provider that was session-deactivated before its credential changed.
        # Re-arm it before the runtime probe so recovery never requires restart.
        if hasattr(brain, "reactivate_provider"):
            brain.reactivate_provider(provider)
        probe = getattr(brain, "tool_model_candidate_status", None)
        if callable(probe):
            verdict = probe(provider, model)
        else:
            verdict = _static_candidate_status(provider, model)
        if not verdict.get("ready"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Provider '{provider}' is not ready for tool calls: "
                    f"{verdict.get('reason', 'unknown')}"
                ),
            )

    if body.persist:
        try:
            from jarvis.core.config_writer import set_tool_model_selection

            set_tool_model_selection(provider, model=model)
        except Exception as exc:  # noqa: BLE001 -- convert config failures to HTTP
            raise HTTPException(status_code=500, detail=f"TOML write failed: {exc}") from exc

    cfg = _config(request)
    tier = BrainTierConfig(provider=provider)
    cfg.brain.tool_model = tier
    cfg.brain.computer_use = tier
    getattr(cfg.brain, "__pydantic_fields_set__", set()).add("tool_model")
    if provider != "auto" and model is not None:
        pc = cfg.brain.providers.get(provider)
        if pc is None:
            pc = BrainProviderConfig()
            cfg.brain.providers[provider] = pc
        pc.tool_model = model or None
        pc.cu_model = model or None

    manager_cfg = getattr(brain, "_config", None)
    if manager_cfg is not None and manager_cfg is not cfg:
        manager_tier = BrainTierConfig(provider=provider)
        manager_cfg.brain.tool_model = manager_tier
        manager_cfg.brain.computer_use = manager_tier
        if provider != "auto" and model is not None:
            pc = manager_cfg.brain.providers.get(provider)
            if pc is None:
                pc = BrainProviderConfig()
                manager_cfg.brain.providers[provider] = pc
            pc.tool_model = model or None
            pc.cu_model = model or None
    await _publish_configured(request)
    status = _status(request)
    status["persisted"] = body.persist
    status["restart_required"] = False
    return ToolModelStatus(**status)
