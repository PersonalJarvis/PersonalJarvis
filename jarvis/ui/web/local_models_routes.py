"""REST surface of the "Local models" section: inventory, unload, delete.

Everything the section shows about what is INSTALLED on the pull-capable
local server (Ollama today) lives here, separate from ``provider_routes.py``
which keeps serving the provider card (model pick, pull, runtime). Every
handler sits behind the same capability gate as the pull routes —
:func:`provider_routes._require_pull_capable` — so a cloud card answers 400
with a sentence, never a silent empty table.

Response bodies are Pydantic models: the Python half of the five-layer
parity contract (AP-4); the TS mirror lives in ``src/hooks/useLocalModels.ts``.

Deleting is the one destructive action, and it is refused — 409 with the
reason — while a configured role still points at the model, unless the
caller names a replacement (``?reassign=``) that is installed; then the
roles are rewritten through the config writers FIRST, so a crash between
the two steps leaves a valid config, never an empty slot.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.brain import ollama_inventory as inventory
from jarvis.brain.ollama_inventory import (
    OllamaModelInfo,
    OllamaModelNotFound,
    OllamaRunningModel,
    OllamaServerError,
    same_model,
)
from jarvis.ui.web.provider_routes import _require_pull_capable, _resolve_cfg

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/providers/{provider_id}/local-models", tags=["local-models"])

#: Role id -> where the brain reads it. The three ``[brain.providers.ollama]``
#: model fields plus the wiki's embedding slot (Ollama-backed when
#: ``embedding_provider == "ollama"``). Chunk 5 grows this into
#: ``ollama_roles.ROLES``; the ids are kept stable for that.
ROLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("chat", "model"),
    ("tools_screen", "tool_model"),
    ("deep", "deep_model"),
)
EMBEDDING_ROLE = "embedding"


# ── Response / body models (AP-4, Python half) ───────────────────────────


class LocalModelRow(BaseModel):
    name: str
    size_bytes: int
    digest: str
    modified_at: str
    family: str
    parameter_size: str
    quantization_level: str
    context_length: int | None
    capabilities: list[str]
    license: str
    probed: bool
    #: Role ids (``chat`` / ``tools_screen`` / ``deep`` / ``embedding``) whose
    #: configured pick is this download.
    used_by: list[str] = Field(default_factory=list)
    #: Present when ``/api/ps`` lists it.
    loaded: bool = False
    size_vram_bytes: int = 0
    expires_at: str = ""
    running_context_length: int | None = None


class LocalModelDetail(LocalModelRow):
    parameters: str = ""
    template: str = ""


class RunningModelRow(BaseModel):
    name: str
    size_bytes: int
    size_vram_bytes: int
    expires_at: str
    context_length: int | None
    digest: str = ""


class InventoryResponse(BaseModel):
    provider: str
    server: str
    models: list[LocalModelRow]
    running: list[RunningModelRow]
    disk_bytes: int
    #: Sum of ``size_vram_bytes`` over the loaded models.
    loaded_vram_bytes: int
    #: English sentence when the server did not answer; the lists are empty
    #: then and the UI shows this instead of "you have nothing installed".
    error: str | None = None


class UnloadResponse(BaseModel):
    ok: bool
    model: str
    message: str


class DeleteResponse(BaseModel):
    ok: bool
    model: str
    message: str
    #: Roles rewritten to ``reassign`` before the delete (empty when none).
    reassigned: list[str] = Field(default_factory=list)
    reassigned_to: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _server_root() -> str:
    from jarvis.brain.ollama_pull import server_root

    return server_root()


def _ollama_provider_cfg(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("ollama") if isinstance(providers, dict) else None


def _roles_using(cfg: Any, name: str) -> list[str]:
    """Role ids whose configured pick is ``name`` (``:latest`` tolerant)."""
    if cfg is None:
        return []
    out: list[str] = []
    provider = _ollama_provider_cfg(cfg)
    for role, field in ROLE_FIELDS:
        value = str(getattr(provider, field, "") or "") if provider is not None else ""
        if value and same_model(value, name):
            out.append(role)
    ultrawiki = getattr(cfg, "ultrawiki", None)
    if (
        ultrawiki is not None
        and str(getattr(ultrawiki, "embedding_provider", "") or "") == "ollama"
        and same_model(str(getattr(ultrawiki, "embedding_model", "") or ""), name)
    ):
        out.append(EMBEDDING_ROLE)
    return out


def _row(
    info: OllamaModelInfo, running: dict[str, OllamaRunningModel], used_by: list[str]
) -> dict[str, Any]:
    live = next((r for key, r in running.items() if same_model(key, info.name)), None)
    return {
        "name": info.name,
        "size_bytes": info.size_bytes,
        "digest": info.digest,
        "modified_at": info.modified_at,
        "family": info.family,
        "parameter_size": info.parameter_size,
        "quantization_level": info.quantization_level,
        "context_length": info.context_length,
        "capabilities": list(info.capabilities),
        "license": info.license,
        "probed": info.probed,
        "used_by": used_by,
        "loaded": live is not None,
        "size_vram_bytes": live.size_vram_bytes if live else 0,
        "expires_at": live.expires_at if live else "",
        "running_context_length": live.context_length if live else None,
    }


async def _running_by_name(root: str) -> dict[str, OllamaRunningModel]:
    """``/api/ps`` as a map; an unanswered probe is an empty map (logged), so a
    server that lists downloads but stalls on ``/api/ps`` still renders."""
    try:
        return {r.name: r for r in await inventory.running_models(root)}
    except OllamaServerError as exc:
        log.info("local-models: /api/ps unavailable at %s: %s", root, exc)
        return {}


def _http_error(exc: OllamaServerError) -> HTTPException:
    if isinstance(exc, OllamaModelNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/inventory", response_model=InventoryResponse)
async def get_inventory(provider_id: str, request: Request) -> InventoryResponse:
    """Every download with its facts, what is loaded, and the disk total.

    Jarvis's own aliases (option profiles, the voice alias) are hidden from
    ``models``; ``running`` still names them, so the memory an alias holds is
    visible and ``loaded_vram_bytes`` stays honest.
    """
    _require_pull_capable(provider_id)
    root = _server_root()
    cfg = _resolve_cfg(request)
    try:
        models = await inventory.list_models(root)
    except OllamaServerError as exc:
        return InventoryResponse(
            provider=provider_id,
            server=root,
            models=[],
            running=[],
            disk_bytes=0,
            loaded_vram_bytes=0,
            error=str(exc),
        )
    running = await _running_by_name(root)
    rows = [LocalModelRow(**_row(m, running, _roles_using(cfg, m.name))) for m in models]
    return InventoryResponse(
        provider=provider_id,
        server=root,
        models=rows,
        running=[RunningModelRow(**asdict(r)) for r in running.values()],
        disk_bytes=sum(m.size_bytes for m in models),
        loaded_vram_bytes=sum(r.size_vram_bytes for r in running.values()),
    )


@router.get("/inventory/{name:path}", response_model=LocalModelDetail)
async def get_inventory_model(provider_id: str, name: str, request: Request) -> LocalModelDetail:
    """One download with the long facts (license, parameters, template)."""
    _require_pull_capable(provider_id)
    root = _server_root()
    try:
        info = await inventory.get_model(root, name)
    except OllamaServerError as exc:
        raise _http_error(exc) from exc
    running = await _running_by_name(root)
    payload = _row(info, running, _roles_using(_resolve_cfg(request), info.name))
    payload["parameters"] = info.parameters
    payload["template"] = info.template
    return LocalModelDetail(**payload)


@router.post(
    "/inventory/{name:path}/unload",
    response_model=UnloadResponse,
    openapi_extra={"x-jarvis-dangerous": True},
)
async def unload_inventory_model(provider_id: str, name: str) -> UnloadResponse:
    """Free the memory a loaded model holds (``keep_alive: 0``).

    Dangerous-flagged because the next turn on that model pays the load time
    again — a voice session mid-conversation notices. Nothing is deleted.
    """
    _require_pull_capable(provider_id)
    root = _server_root()
    try:
        await inventory.unload_model(root, name)
    except OllamaServerError as exc:
        raise _http_error(exc) from exc
    return UnloadResponse(
        ok=True,
        model=name,
        message=f"{name} was unloaded; its memory is free until the next turn uses it.",
    )


@router.delete(
    "/inventory/{name:path}",
    response_model=DeleteResponse,
    openapi_extra={"x-jarvis-dangerous": True},
)
async def delete_inventory_model(
    provider_id: str,
    name: str,
    request: Request,
    reassign: str | None = Query(
        default=None,
        description=(
            "Installed model that takes over every role currently pointing at "
            "the deleted one. Required when a role points at it."
        ),
    ),
) -> DeleteResponse:
    """Remove a download from the server (``DELETE /api/delete``).

    409 while a configured role (chat / tools & screen / deep / embeddings)
    still names the model and no ``reassign`` is given; with one, the roles
    are rewritten through the config writers first, then the delete runs.
    """
    _require_pull_capable(provider_id)
    root = _server_root()
    cfg = _resolve_cfg(request)
    roles = _roles_using(cfg, name)
    reassigned: list[str] = []
    target = (reassign or "").strip()
    if roles and not target:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{name} is still the pick for {', '.join(roles)}. Choose another "
                "installed model for those roles first, or pass ?reassign=<model>."
            ),
        )
    if roles and target:
        if same_model(target, name):
            raise HTTPException(
                status_code=422,
                detail="The replacement must be a different model than the one being deleted.",
            )
        try:
            installed = await inventory.list_models(root)
        except OllamaServerError as exc:
            raise _http_error(exc) from exc
        if not any(same_model(m.name, target) for m in installed):
            raise HTTPException(
                status_code=422,
                detail=f"{target} is not installed on the server, so it cannot take over.",
            )
        reassigned = _reassign_roles(cfg, roles, target)
    try:
        await inventory.delete_model(root, name)
    except OllamaServerError as exc:
        raise _http_error(exc) from exc
    message = f"{name} was deleted from the server."
    if reassigned:
        message += f" {', '.join(reassigned)} now use {target}."
    return DeleteResponse(
        ok=True,
        model=name,
        message=message,
        reassigned=reassigned,
        reassigned_to=target or None,
    )


def _reassign_roles(cfg: Any, roles: list[str], target: str) -> list[str]:
    """Persist ``target`` for every role in ``roles`` and mirror it into the
    live config object so the next read agrees with the TOML."""
    from jarvis.core import config_writer

    fields = [field for role, field in ROLE_FIELDS if role in roles]
    done: list[str] = []
    try:
        if fields:
            config_writer.set_brain_provider_model(
                "ollama",
                model=target if "model" in fields else None,
                deep_model=target if "deep_model" in fields else None,
                tool_model=target if "tool_model" in fields else None,
            )
            provider = _ollama_provider_cfg(cfg)
            for field in fields:
                if provider is not None:
                    setattr(provider, field, target)
            done.extend(role for role, field in ROLE_FIELDS if field in fields)
        if EMBEDDING_ROLE in roles:
            config_writer.set_ultrawiki_slot("embedding_model", target)
            ultrawiki = getattr(cfg, "ultrawiki", None)
            if ultrawiki is not None:
                ultrawiki.embedding_model = target
            done.append(EMBEDDING_ROLE)
    except Exception as exc:  # noqa: BLE001 — the delete must not run on a half-written config
        log.warning("local-models: reassigning %s to %s failed: %s", roles, target, exc)
        raise HTTPException(
            status_code=500, detail=f"Could not rewrite the roles to {target}: {exc}"
        ) from exc
    return done
