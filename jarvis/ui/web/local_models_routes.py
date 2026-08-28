"""REST surface of the "Local models" section.

Inventory, unload and delete; the four roles; per-model options and the
suggestion for this machine; the public catalogue; Hugging Face GGUF
browsing (off until switched on); and the server itself (status, stop,
probe, log, environment guide).

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

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.brain import ollama_inventory as inventory
from jarvis.brain import (
    ollama_library,
    ollama_overview,
    ollama_profiles,
    ollama_pull,
    ollama_roles,
    ollama_runtime,
)
from jarvis.brain.ollama_inventory import (
    OllamaModelNotFound,
    OllamaRunningModel,
    OllamaServerError,
    same_model,
)
from jarvis.core import config as cfg_mod
from jarvis.core import config_writer
from jarvis.core.config import OLLAMA_MODEL_OPTION_KEYS, OllamaModelOptions
from jarvis.ui.web.provider_routes import _require_pull_capable, _resolve_cfg

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/providers/{provider_id}/local-models", tags=["local-models"])

# ── Response / body models (AP-4, Python half) ───────────────────────────


class LocalModelRow(BaseModel):
    name: str
    #: Readable parts from ``jarvis.brain.ollama_names.describe``: the model
    #: line ("Qwen 3.5"), the line with its size ("Qwen 3.5 4B"), the size
    #: ("4B"), the quantisation ("Q4_K_M"), the source namespace for imports
    #: ("hf.co/unsloth") and the tag's variant markers ("it-qat").
    display_name: str = ""
    display_label: str = ""
    params_label: str = ""
    quant_label: str = ""
    source: str = ""
    variant: str = ""
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
    #: Present when ``/api/ps`` lists it — the download itself or one of its
    #: aliases (``loaded_as`` names which; a Tune profile or the voice
    #: brain's context alias holds the same weights).
    loaded: bool = False
    loaded_as: str = ""
    size_vram_bytes: int = 0
    #: What ``/api/ps`` reports as the loaded size (weights + context).
    loaded_size_bytes: int = 0
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
    #: The download this entry's weights belong to (an alias folded back).
    base_tag: str = ""
    #: ``model`` | ``tune_profile`` | ``voice_profile``.
    kind: str = "model"


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


def _forget_overview() -> None:
    """Drop the memoised overview after a write, so the next paint is honest."""
    ollama_overview.forget(_server_root())


def _server_root() -> str:
    from jarvis.brain.ollama_pull import server_root

    return server_root()


def _ollama_provider_cfg(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get("ollama") if isinstance(providers, dict) else None


def _roles_using(cfg: Any, name: str) -> list[str]:
    """Role ids whose configured pick is ``name`` (``:latest`` tolerant).

    Delegates to :func:`jarvis.brain.ollama_roles.roles_using` so the ledger's
    "Used by" badges and the delete refusal agree with the Roles panel — the
    voice slot (read from the voice server's launch command) included.
    """
    if cfg is None:
        return []
    from jarvis.brain.ollama_roles import roles_using

    return roles_using(cfg, name)


def _readonly_roles_using(cfg: Any, name: str) -> list[str]:
    """The read-only consumers (quick ack, dictation polish) pinned to ``name``.

    They cannot be reassigned from here — their pick lives on their own
    card — so a delete does not refuse for them, but it says so, because
    the model vanishing under them is otherwise a silent breakage.
    """
    if cfg is None:
        return []
    from jarvis.brain.ollama_roles import roles_using

    writable = set(roles_using(cfg, name))
    return [r for r in roles_using(cfg, name, include_readonly=True) if r not in writable]


async def _running_by_name(root: str) -> dict[str, OllamaRunningModel]:
    """``/api/ps`` as a map from the shared snapshot; an unanswered probe is an
    empty map (logged), so a server that lists downloads but stalls on
    ``/api/ps`` still renders."""
    try:
        snapshot = await inventory.cached_snapshot(root)
    except OllamaServerError as exc:
        log.info("local-models: inventory unavailable at %s: %s", root, exc)
        return {}
    return {r.name: r for r in snapshot.running}


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
    payload = await ollama_overview.inventory_payload(
        provider_id, _server_root(), _resolve_cfg(request)
    )
    return InventoryResponse(**payload)


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
    payload = ollama_overview.model_row(
        info, running, _roles_using(_resolve_cfg(request), info.name)
    )
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
    _forget_overview()
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
    _forget_overview()
    message = f"{name} was deleted from the server."
    if reassigned:
        message += f" {', '.join(reassigned)} now use {target}."
    readonly = _readonly_roles_using(cfg, name)
    if readonly:
        from jarvis.brain.ollama_roles import role_spec

        where = ", ".join(f"{r} ({role_spec(r).config_key})" for r in readonly)
        message += f" Still named by {where} — change that on its own card."
    return DeleteResponse(
        ok=True,
        model=name,
        message=message,
        reassigned=reassigned,
        reassigned_to=target or None,
    )


def _reassign_roles(cfg: Any, roles: list[str], target: str) -> list[str]:
    """Persist ``target`` for every role in ``roles`` through the roles module.

    One writer per slot lives in :mod:`jarvis.brain.ollama_roles` (chat, voice,
    tools/screen, deep, embedding); going through it keeps the delete path
    honest for the voice slot, whose pick is not a plain config field.
    """
    from jarvis.brain.ollama_roles import set_role

    done: list[str] = []
    try:
        for role in roles:
            set_role(role, target, cfg=cfg)
            done.append(role)
    except Exception as exc:  # noqa: BLE001 — the delete must not run on a half-written config
        log.warning("local-models: reassigning %s to %s failed: %s", roles, target, exc)
        raise HTTPException(
            status_code=500, detail=f"Could not rewrite the roles to {target}: {exc}"
        ) from exc
    return done


# ═════════════════════════════════════════════════════════════════════════
# Roles
# ═════════════════════════════════════════════════════════════════════════


class RoleChoice(BaseModel):
    """One installed download judged for one job — the picker's row."""

    tag: str
    #: ``fits`` | ``slow`` | ``unfit`` | ``unknown`` (``ollama_roles.FITS``).
    fit: str
    #: Short clause saying why, ``""`` when it fits.
    reason: str = ""


class RoleDownload(BaseModel):
    """A shortlist entry that would do the job here and is not on disk."""

    tag: str
    label: str
    size_gb: float
    #: ``comfortable`` | ``tight`` | ``unknown`` — the shortlist's own verdict.
    fit: str
    note: str = ""


class RoleRow(BaseModel):
    id: str
    label_key: str
    config_key: str
    #: ``card`` | ``row`` | ``footnote`` — where the section shows it.
    layout: str = "card"
    #: Configured tag; ``""`` = the plugin discovers one.
    current: str
    #: ``current`` is on the server right now.
    installed: bool
    #: The verdict on ``current``: a fit from ``ollama_roles.FITS``, ``absent``
    #: when configured but not on the server, ``""`` when nothing is set.
    current_fit: str = ""
    current_reason: str = ""
    required: list[str]
    recommended_capabilities: list[str]
    #: Installed tags that can do the job (``fits`` or ``slow``).
    qualifying: list[str]
    #: Every installed download with its verdict, inventory order.
    choices: list[RoleChoice] = Field(default_factory=list)
    #: Shortlist downloads that would do the job on this machine.
    downloads: list[RoleDownload] = Field(default_factory=list)
    #: The pick for this machine — the best qualifying INSTALLED download,
    #: or the shortlist's download when nothing installed qualifies (``""``
    #: when neither has one).
    recommended: str
    #: One sentence saying why ``recommended`` is the pick (``""`` when none).
    recommended_reason: str = ""
    writable: bool
    advanced: bool
    #: One sentence when the slot is served by something other than Ollama.
    note: str = ""
    #: The voice brain's effective context on this machine (voice role only).
    context_tokens: int | None = None
    #: "automatic" | "manual" | "".
    context_source: str = ""
    #: The size class the job prefers, in GB (the voice brain stays under 6
    #: so a call answers within a breath); ``None`` when the job has none.
    max_size_gb: float | None = None
    #: The smallest native context that can do the job; ``None`` = no floor.
    min_context_tokens: int | None = None


class ResidentItem(BaseModel):
    """One distinct download across the roles, with what it costs loaded."""

    tag: str
    display_label: str = ""
    roles: list[str]
    weights_gb: float
    context_gb: float
    context_tokens: int | None = None
    loaded: bool = False


class ResidentPayload(BaseModel):
    """What graphics memory holds when every job is loaded at once."""

    items: list[ResidentItem] = Field(default_factory=list)
    #: Kept free beside the voice brain for local speech in/out.
    reserve_gb: float = 0.0
    total_gb: float = 0.0
    accelerator_gb: float = 0.0
    over: bool = False


class RolesResponse(BaseModel):
    provider: str
    server: str
    roles: list[RoleRow]
    resident: ResidentPayload = Field(default_factory=ResidentPayload)
    error: str | None = None


class RoleSetBody(BaseModel):
    #: ``""`` = back to discovery (brain roles only).
    model: str = Field(default="", max_length=200)


class RoleSetResponse(BaseModel):
    ok: bool
    role: str
    model: str
    config_key: str
    message: str


@router.get("/roles", response_model=RolesResponse)
async def get_roles(provider_id: str, request: Request) -> RolesResponse:
    """Every model slot with its pick, what qualifies, and the recommendation."""
    _require_pull_capable(provider_id)
    payload = await ollama_overview.roles_payload(
        provider_id, _server_root(), _resolve_cfg(request)
    )
    return RolesResponse(**payload)


@router.put("/roles/{role}", response_model=RoleSetResponse)
async def set_role(
    provider_id: str, role: str, body: RoleSetBody, request: Request
) -> RoleSetResponse:
    """Assign ``model`` to a role through the config writers; ``""`` = discovery."""
    _require_pull_capable(provider_id)
    cfg = _resolve_cfg(request)
    try:
        written = ollama_roles.set_role(role, body.model, cfg=cfg)
    except ValueError as exc:
        status = 404 if "Unknown role" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a failed TOML write is a 500 with the reason
        log.warning("local-models: role %s -> %s failed: %s", role, body.model, exc)
        raise HTTPException(status_code=500, detail=f"Could not save the role: {exc}") from exc
    _forget_overview()
    tag = written["model"]
    message = (
        f"{role} now uses {tag}."
        if tag
        else f"{role} is back to discovery: Jarvis picks the smallest capable download."
    )
    if not written.get("drift_guarded", True):
        message += (
            " This pick may be reverted by the drift guard — "
            "the config baseline could not be updated."
        )
    return RoleSetResponse(
        ok=True, role=role, model=tag, config_key=written["config_key"], message=message
    )


# ═════════════════════════════════════════════════════════════════════════
# Per-model options
# ═════════════════════════════════════════════════════════════════════════


class OllamaModelOptionsBody(BaseModel):
    """The PUT body — one field per :data:`OLLAMA_MODEL_OPTION_KEYS`, in order.

    ``tests/unit/core/test_ollama_model_options_parity.py`` pins the field
    list against the tuple. Values are clamped by the writer, never rejected.
    """

    num_ctx: int | None = None
    num_gpu: int | None = None
    num_thread: int | None = None
    num_predict: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    stop: list[str] | str | None = None
    keep_alive: str | int | None = None
    think: bool | str | None = None


assert tuple(OllamaModelOptionsBody.model_fields) == OLLAMA_MODEL_OPTION_KEYS


class ModelOptionsResponse(BaseModel):
    model: str
    #: Only the knobs that are set.
    options: dict[str, Any]
    #: True when ``[brain.providers.ollama.models."<tag>"]`` exists.
    configured: bool
    #: The derived alias the brain streams through when a bakeable knob is
    #: set; ``null`` when the options ride the request alone.
    profile_alias: str | None = None


class SuggestedOptionsResponse(BaseModel):
    model: str
    options: dict[str, Any]
    #: One plain sentence per knob (and one for the memory budget).
    reasons: list[str]
    size_gb: float
    native_context: int | None
    accelerator_gb: float
    accelerator_source: str
    ram_gb: float | None


def _options_for(cfg: Any, name: str) -> tuple[str | None, OllamaModelOptions | None]:
    """``(stored key, options)`` for ``name`` (``:latest`` tolerant)."""
    provider = _ollama_provider_cfg(cfg)
    models = getattr(provider, "models", None) or {}
    if not isinstance(models, dict):
        return None, None
    for key, value in models.items():
        if same_model(str(key), name) and isinstance(value, OllamaModelOptions):
            return str(key), value
    return None, None


def _options_response(name: str, opts: OllamaModelOptions | None) -> ModelOptionsResponse:
    if opts is None:
        return ModelOptionsResponse(model=name, options={}, configured=False)
    compact = opts.model_dump(exclude_none=True)
    alias = ollama_profiles.profile_name(name, opts) if ollama_profiles.has_bakeable(opts) else None
    return ModelOptionsResponse(model=name, options=compact, configured=True, profile_alias=alias)


@router.get("/models/{name:path}/options", response_model=ModelOptionsResponse)
async def get_model_options(provider_id: str, name: str, request: Request) -> ModelOptionsResponse:
    """The per-model profile as configured (empty when none)."""
    _require_pull_capable(provider_id)
    _key, opts = _options_for(_resolve_cfg(request), name)
    return _options_response(name, opts)


@router.put("/models/{name:path}/options", response_model=ModelOptionsResponse)
async def put_model_options(
    provider_id: str, name: str, body: OllamaModelOptionsBody, request: Request
) -> ModelOptionsResponse:
    """Replace the profile of ``name`` with ``body`` (whole set; nothing set = clear)."""
    _require_pull_capable(provider_id)
    tag = name.strip()
    if not tag:
        raise HTTPException(status_code=422, detail="A model name is needed.")
    raw = {k: v for k, v in body.model_dump().items() if v is not None}
    from jarvis.core import config_writer

    try:
        written = config_writer.set_ollama_model_options(tag, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a failed TOML write is a 500 with the reason
        log.warning("local-models: options for %s failed: %s", tag, exc)
        raise HTTPException(status_code=500, detail=f"Could not save the options: {exc}") from exc
    cfg = _resolve_cfg(request)
    provider = _ollama_provider_cfg(cfg)
    opts = OllamaModelOptions.model_validate(written) if written else None
    if provider is not None and isinstance(getattr(provider, "models", None), dict):
        stored_key, _old = _options_for(cfg, tag)
        if stored_key is not None:
            provider.models.pop(stored_key, None)
        if opts is not None:
            provider.models[tag] = opts
    _forget_overview()
    return _options_response(tag, opts)


@router.delete("/models/{name:path}/options", response_model=ModelOptionsResponse)
async def delete_model_options(
    provider_id: str, name: str, request: Request
) -> ModelOptionsResponse:
    """Reset: drop the profile so Ollama's defaults apply again."""
    _require_pull_capable(provider_id)
    from jarvis.core import config_writer

    try:
        config_writer.clear_ollama_model_options(name)
    except Exception as exc:  # noqa: BLE001 — a failed TOML write is a 500 with the reason
        log.warning("local-models: clearing options for %s failed: %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Could not reset the options: {exc}") from exc
    cfg = _resolve_cfg(request)
    stored_key, _old = _options_for(cfg, name)
    provider = _ollama_provider_cfg(cfg)
    if stored_key is not None and provider is not None:
        provider.models.pop(stored_key, None)
    _forget_overview()
    return _options_response(name, None)


@router.get("/models/{name:path}/suggested-options", response_model=SuggestedOptionsResponse)
async def get_suggested_options(provider_id: str, name: str) -> SuggestedOptionsResponse:
    """An advisory profile for ``name`` on THIS machine, with one reason per knob."""
    _require_pull_capable(provider_id)
    root = _server_root()
    try:
        info = await inventory.get_model(root, name)
    except OllamaServerError as exc:
        raise _http_error(exc) from exc
    accel, source = ollama_pull.accelerator_gb()
    ram = ollama_pull.total_memory_gb()
    size_gb = round(info.size_bytes / (1024**3), 2)
    opts, reasons = ollama_profiles.suggest_options(
        size_gb=size_gb,
        native_context=info.context_length,
        accelerator_gb=accel,
        source=source,
        ram_gb=ram,
    )
    return SuggestedOptionsResponse(
        model=info.name,
        options=opts.model_dump(exclude_none=True),
        reasons=reasons,
        size_gb=size_gb,
        native_context=info.context_length,
        accelerator_gb=round(accel, 1),
        accelerator_source=source,
        ram_gb=ram,
    )


# ═════════════════════════════════════════════════════════════════════════
# Catalogue
# ═════════════════════════════════════════════════════════════════════════


@router.get("/catalog")
async def get_catalog(
    provider_id: str,
    q: str = Query(default="", max_length=120),
    sort: str = Query(default="popular", description="popular | newest"),
    capability: str | None = Query(
        default=None, description="tools | vision | embedding | thinking"
    ),
    limit: int = Query(default=50, ge=1, le=50),
) -> dict[str, Any]:
    """Browse ollama.com; ``error`` is a normal outcome offline, never a 5xx."""
    _require_pull_capable(provider_id)
    return await ollama_library.search_library(q, sort=sort, capability=capability, limit=limit)


@router.get("/catalog/recommended")
async def get_catalog_recommended(provider_id: str) -> dict[str, Any]:
    """The curated shortlist ranked for this machine, with its review date."""
    _require_pull_capable(provider_id)
    return await ollama_pull.recommendations()


@router.get("/catalog/{name:path}/tags")
async def get_catalog_tags(provider_id: str, name: str) -> dict[str, Any]:
    """Every tag of one library model with size, quantization, context and fit."""
    _require_pull_capable(provider_id)
    return await ollama_library.library_tags(name)


# ═════════════════════════════════════════════════════════════════════════
# Hugging Face (off until switched on)
# ═════════════════════════════════════════════════════════════════════════


class HfEnabledBody(BaseModel):
    enabled: bool


class HfEnabledResponse(BaseModel):
    enabled: bool


class HfPullBody(BaseModel):
    user: str = Field(max_length=96)
    repo: str = Field(max_length=96)
    quant: str | None = Field(default=None, max_length=96)


_HF_OFF = (
    "Hugging Face browsing is switched off. Turn it on under Local models → "
    "Hugging Face; until then Jarvis makes no request to huggingface.co."
)


def _require_hf(request: Request) -> None:
    if not cfg_mod.ollama_hf_enabled(_resolve_cfg(request)):
        raise HTTPException(status_code=404, detail=_HF_OFF)


@router.get("/hf/enabled", response_model=HfEnabledResponse)
async def get_hf_enabled(provider_id: str, request: Request) -> HfEnabledResponse:
    _require_pull_capable(provider_id)
    return HfEnabledResponse(enabled=cfg_mod.ollama_hf_enabled(_resolve_cfg(request)))


@router.put("/hf/enabled", response_model=HfEnabledResponse)
async def put_hf_enabled(
    provider_id: str, body: HfEnabledBody, request: Request
) -> HfEnabledResponse:
    """Switch Hugging Face browsing on or off (``[brain.providers.ollama].hf_enabled``)."""
    _require_pull_capable(provider_id)
    from jarvis.core import config_writer

    try:
        config_writer.set_ollama_hf_enabled(body.enabled)
    except Exception as exc:  # noqa: BLE001 — a failed TOML write is a 500 with the reason
        log.warning("local-models: hf_enabled=%s failed: %s", body.enabled, exc)
        raise HTTPException(status_code=500, detail=f"Could not save the switch: {exc}") from exc
    provider = _ollama_provider_cfg(_resolve_cfg(request))
    if provider is not None:
        provider.hf_enabled = body.enabled
    return HfEnabledResponse(enabled=body.enabled)


@router.get("/hf/search")
async def get_hf_search(
    provider_id: str,
    request: Request,
    q: str = Query(default="", max_length=120),
    sort: str = Query(default="downloads", description="downloads | lastModified | trendingScore"),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    """GGUF repositories on Hugging Face; ``error`` is a sentence, never a 5xx."""
    _require_pull_capable(provider_id)
    _require_hf(request)
    from jarvis.brain import hf_gguf  # lazy: only an install that switched HF on imports it

    return await hf_gguf.search(q, sort=sort, limit=limit)


@router.get("/hf/{user}/{repo}/files")
async def get_hf_files(provider_id: str, user: str, repo: str, request: Request) -> dict[str, Any]:
    """The ``.gguf`` files of one repository with quantization, size and fit."""
    _require_pull_capable(provider_id)
    _require_hf(request)
    from jarvis.brain import hf_gguf

    return await hf_gguf.files(user, repo)


@router.post("/hf/pull")
async def post_hf_pull(provider_id: str, body: HfPullBody, request: Request) -> dict[str, Any]:
    """Start pulling ``hf.co/<user>/<repo>[:<quant>]`` through the normal pull path."""
    _require_pull_capable(provider_id)
    _require_hf(request)
    from jarvis.brain import hf_gguf

    try:
        name = hf_gguf.pull_name(body.user, body.repo, body.quant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await ollama_pull.start_pull(name)


# ═════════════════════════════════════════════════════════════════════════
# Server
# ═════════════════════════════════════════════════════════════════════════


class ServerResponse(BaseModel):
    installed: bool
    binary: str
    running: bool
    #: Spawned by this install and not answering yet — the boot window the
    #: panel shows as "Starting" instead of the misleading "Stopped".
    starting: bool = False
    version: str
    detail: str
    base_url: str
    host_kind: str
    models_dir: str
    running_models: list[RunningModelRow] = Field(default_factory=list)
    disk_bytes: int = 0
    loaded_vram_bytes: int = 0
    #: Sentence when the inventory could not be read while the probe ran.
    error: str | None = None


class ServerActionResponse(BaseModel):
    ok: bool
    message: str


class ServerTestBody(BaseModel):
    base_url: str = Field(max_length=300)


class ServerProbeResponse(BaseModel):
    ok: bool
    version: str
    latency_ms: int
    detail: str


class ServerLogResponse(BaseModel):
    lines: list[str]


class EnvGuideRow(BaseModel):
    key: str
    purpose: str
    command: str
    restart: str


class EnvGuideResponse(BaseModel):
    os: str
    rows: list[EnvGuideRow]


@router.get("/server", response_model=ServerResponse)
async def get_server(provider_id: str) -> ServerResponse:
    """Runtime picture plus what is loaded and how much disk the downloads take."""
    _require_pull_capable(provider_id)
    # The synchronous runtime probe (up to 1.5 s when the server is down)
    # runs off the loop inside ``server_payload``.
    return ServerResponse(**await ollama_overview.server_payload(_server_root()))


@router.post(
    "/server/stop",
    response_model=ServerActionResponse,
    openapi_extra={"x-jarvis-dangerous": True},
)
async def post_server_stop(provider_id: str) -> ServerActionResponse:
    """Stop the Ollama Jarvis itself started — never one started elsewhere."""
    _require_pull_capable(provider_id)
    ok, message = await asyncio.to_thread(ollama_runtime.stop_server)
    return ServerActionResponse(ok=ok, message=message)


class IdleReleaseBody(BaseModel):
    #: Minutes of voice idleness before the local stack frees the accelerator; 0 = never.
    minutes: int = Field(ge=0, le=1440)


class IdleReleaseResponse(BaseModel):
    minutes: int


@router.get("/runtime/idle-release", response_model=IdleReleaseResponse)
async def get_idle_release(provider_id: str, request: Request) -> IdleReleaseResponse:
    """How long the local voice stack may sit idle before it releases memory."""
    _require_pull_capable(provider_id)
    voice = getattr(_resolve_cfg(request), "voice", None)
    return IdleReleaseResponse(minutes=int(getattr(voice, "local_idle_release_minutes", 30)))


@router.put("/runtime/idle-release", response_model=IdleReleaseResponse)
async def put_idle_release(
    provider_id: str, body: IdleReleaseBody, request: Request
) -> IdleReleaseResponse:
    """Persist the idle window; the supervisor reads it on its next tick."""
    _require_pull_capable(provider_id)
    config_writer.set_local_idle_release_minutes(body.minutes)
    voice = getattr(_resolve_cfg(request), "voice", None)
    if voice is not None:
        try:
            voice.local_idle_release_minutes = int(body.minutes)
        except Exception:  # noqa: BLE001 — a frozen in-memory config still got the TOML write
            log.debug("local-models: in-memory idle-release update skipped", exc_info=True)
    return IdleReleaseResponse(minutes=int(body.minutes))


class AutostartResponse(BaseModel):
    #: ``[brain.providers.ollama].autostart`` — start the server with Jarvis.
    enabled: bool
    #: Whether anything in this install runs on the local server right now
    #: (the active brain, or a configured role); the boot task starts
    #: nothing while this is false, whatever ``enabled`` says.
    in_use: bool
    #: One sentence: what the boot task would do and why.
    reason: str


class AutostartBody(BaseModel):
    enabled: bool


@router.get("/runtime/autostart", response_model=AutostartResponse)
async def get_autostart(provider_id: str, request: Request) -> AutostartResponse:
    """Whether the local server starts with Jarvis, and whether that would fire."""
    _require_pull_capable(provider_id)
    from jarvis.core.config import ollama_autostart
    from jarvis.local_models import autostart

    cfg = _resolve_cfg(request)
    used, _why = autostart.in_use(cfg)
    _start, reason = autostart.should_autostart(cfg)
    return AutostartResponse(enabled=ollama_autostart(cfg), in_use=used, reason=reason)


@router.put("/runtime/autostart", response_model=AutostartResponse)
async def put_autostart(
    provider_id: str, body: AutostartBody, request: Request
) -> AutostartResponse:
    """Switch local models on or off — persisted AND applied to what runs now.

    Off means off (BUG-204): the switch stops the managed voice server,
    unloads every resident model and stops the server this install started,
    instead of only deciding what the NEXT launch does. On readies the
    server the same way picking the local brain does, so one click is the
    whole gesture in both directions.
    """
    _require_pull_capable(provider_id)
    from jarvis.local_models import autostart

    cfg = _resolve_cfg(request)
    config_writer.set_ollama_autostart(body.enabled)
    provider = _ollama_provider_cfg(cfg)
    if provider is not None:
        try:
            provider.autostart = bool(body.enabled)
        except Exception:  # noqa: BLE001 — a frozen config object still has the TOML write
            log.debug("local-models: in-memory autostart update skipped", exc_info=True)
    if body.enabled:
        autostart.kick(cfg)
    else:
        autostart.release(cfg)
    used, _why = autostart.in_use(cfg)
    _start, reason = autostart.should_autostart(cfg)
    return AutostartResponse(enabled=bool(body.enabled), in_use=used, reason=reason)


class VerifyStep(BaseModel):
    #: ``server`` | ``chat`` | ``voice`` | ``tools_screen`` | ``embedding``.
    id: str
    #: ``None`` = not run (the role is not configured, or the server is down).
    ok: bool | None
    model: str = ""
    detail: str = ""
    ms: int = 0


class VerifyResponse(BaseModel):
    ok: bool
    #: The badge's vocabulary: ``ok`` | ``needs_setup`` | ``error``.
    status: str
    reason: str
    steps: list[VerifyStep]


@router.post("/verify", response_model=VerifyResponse)
async def post_verify(provider_id: str, request: Request) -> VerifyResponse:
    """Prove the setup works — the server, one real chat answer, one real
    embedding — and refresh the sidebar's health badge with the result."""
    _require_pull_capable(provider_id)
    from jarvis.local_models import health_monitor

    result = await health_monitor.verify_setup(_resolve_cfg(request))
    return VerifyResponse(**result)


@router.post("/server/test", response_model=ServerProbeResponse)
async def post_server_test(provider_id: str, body: ServerTestBody) -> ServerProbeResponse:
    """Probe a host before saving it: version and latency, or the reason it failed."""
    _require_pull_capable(provider_id)
    probe = await ollama_runtime.probe_host(body.base_url)
    return ServerProbeResponse(
        ok=bool(probe.get("ok")),
        version=str(probe.get("version") or ""),
        latency_ms=int(str(probe.get("latency_ms") or 0)),
        detail=str(probe.get("detail") or ""),
    )


@router.get("/server/log", response_model=ServerLogResponse)
async def get_server_log(
    provider_id: str, lines: int = Query(default=40, ge=1, le=500)
) -> ServerLogResponse:
    """The last lines of the server log Jarvis writes when it starts Ollama."""
    _require_pull_capable(provider_id)
    return ServerLogResponse(lines=await asyncio.to_thread(ollama_runtime.tail_log, lines))


class OverviewResponse(BaseModel):
    """ONE payload for the section's first paint (TS mirror ``OverviewResponse``
    in ``src/hooks/useLocalModels.ts``; parity-tested).

    ``server`` / ``roles`` / ``inventory`` / ``recommended`` are exactly what
    the four single endpoints answer; ``source`` says whether this is a live
    build or the disk snapshot from a previous open (``"cache"``, with a
    refresh already running); ``fetched_at`` is the build time, epoch seconds.
    """

    server: ServerResponse
    roles: RolesResponse
    inventory: InventoryResponse
    recommended: dict[str, Any]
    source: str
    fetched_at: float


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    provider_id: str,
    request: Request,
    fresh: int = Query(default=0, ge=0, le=1, description="1 = build live, skip the cache"),
) -> OverviewResponse:
    """Server, roles, inventory and the shortlist in one answer, cache first.

    The last good overview is kept on disk; it is answered at once as
    ``source: "cache"`` while one background refresh runs, so the section
    paints before the server has even been asked. ``?fresh=1`` forces a live
    build (the section uses it after a change it made itself).
    """
    _require_pull_capable(provider_id)
    payload, source = await ollama_overview.get_overview(
        _server_root(), _resolve_cfg(request), provider_id=provider_id, fresh=bool(fresh)
    )
    return OverviewResponse(
        server=ServerResponse(**payload["server"]),
        roles=RolesResponse(**payload["roles"]),
        inventory=InventoryResponse(**payload["inventory"]),
        recommended=dict(payload.get("recommended") or {}),
        source=source,
        fetched_at=float(payload.get("fetched_at") or 0.0),
    )


@router.get("/server/env-guide", response_model=EnvGuideResponse)
async def get_server_env_guide(
    provider_id: str,
    os: str = Query(default="", description="windows | macos | linux; empty = this OS"),
) -> EnvGuideResponse:
    """Copyable per-OS commands for the server's environment variables."""
    _require_pull_capable(provider_id)
    rows = ollama_runtime.env_guide(os or None)
    return EnvGuideResponse(
        os=ollama_runtime._normalize_os(os or None), rows=[EnvGuideRow(**row) for row in rows]
    )
