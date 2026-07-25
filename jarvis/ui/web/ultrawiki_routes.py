"""UltraWiki REST surface — status, activation, providers, sources, sync jobs,
areas, and hybrid search (CLI-first contract).

Every handler function name doubles as the ``jarvis api ultrawiki <op>`` CLI
command name, so handlers are named like commands. The routes are a thin shell
over :class:`jarvis.ultrawiki.service.UltraWikiService` held on
``app.state.ultrawiki`` (wired in ``WebServer.start()``; ``None`` while the app
is still starting or when init failed — routes then answer 503 honestly).

Mode discipline: ``GET /status`` ALWAYS answers, even while the mode is off —
it is the honesty surface. Search answers 409 (not 503) while the mode switch
is off, because the app itself is healthy; the normal wiki is the one
answering. Heavy modules (store, embeddings, search) are imported lazily
inside the handlers (AP-26) — importing this module stays boot-cheap.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ultrawiki", tags=["ultrawiki"])

_MODE_OFF_DETAIL = "UltraWiki mode is off — the normal wiki answers today."

#: The flat ``[ultrawiki]`` slot keys the settings surface may change.
_SLOT_KEYS = (
    "db_backend",
    "storage_provider",
    "embedding_provider",
    "embedding_model",
    "distill_provider",
    "distill_model",
    "rerank_provider",
    "rerank_model",
    "ollama_endpoint",
)

#: Numeric ranking knobs of the read path (design: UltraWiki ranking
#: pipeline). Kept apart from the provider slots above because they are
#: floats, not names — and because an out-of-range value must be refused here
#: rather than silently ranking nonsense.
_RANKING_KEYS = (
    "rerank_min_score",
    "rrf_keyword_weight",
    "rrf_vector_weight",
    "recency_half_life_days",
)

#: Inclusive bounds per ranking knob.
_RANKING_BOUNDS: dict[str, tuple[float, float]] = {
    "rerank_min_score": (0.0, 10.0),  # the shared 0-10 relevance scale
    "rrf_keyword_weight": (0.0, 10.0),
    "rrf_vector_weight": (0.0, 10.0),
    "recency_half_life_days": (0.0, 36500.0),  # a century is "effectively off"
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")

__all__ = ["router"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(request: Request) -> Any:
    return getattr(request.app.state, "config", None)


def _uw_cfg(request: Request) -> Any:
    return getattr(_config(request), "ultrawiki", None)


def _service(request: Request) -> Any:
    """The UltraWikiService from app.state, or an honest 503 while unwired."""
    service = getattr(request.app.state, "ultrawiki", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "the UltraWiki service is not wired yet — the app is still "
                "starting, or its init failed (check the logs) — retry in a "
                "moment"
            ),
        )
    return service


def _require_active(request: Request) -> Any:
    """The service, but 409 (not 503) while the mode switch is off.

    409 because nothing is broken: the app deliberately answers through the
    normal wiki until UltraWiki mode is activated.
    """
    service = _service(request)
    if not bool(getattr(_uw_cfg(request), "enabled", False)):
        raise HTTPException(status_code=409, detail=_MODE_OFF_DETAIL)
    return service


async def _store_of(service: Any) -> Any:
    """The service's opened store (503 when it could not open).

    The service facade does not yet expose a public store accessor, so the
    area/delete routes reach it through the private attribute after
    ``ensure_started()`` — a documented seam, not an invitation.
    """
    await service.ensure_started()
    store = getattr(service, "_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the UltraWiki store did not open — check the logs",
        )
    return store


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "area"


def _ranking_changes(body: Any, uw: Any) -> dict[str, float]:
    """Validated, actually-changing numeric ranking knobs.

    Bounds are enforced HERE rather than clamped later: a rejected 400 tells
    the user their value was refused, while a silent clamp would leave the UI
    showing a number the ranking does not use.
    """
    changes: dict[str, float] = {}
    for key in _RANKING_KEYS:
        raw = getattr(body, key, None)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"{key} must be a number, got {raw!r}"
            ) from exc
        low, high = _RANKING_BOUNDS[key]
        if not math.isfinite(value) or not (low <= value <= high):
            raise HTTPException(
                status_code=400,
                detail=f"{key} must be between {low} and {high}, got {value}",
            )
        try:
            current = float(getattr(uw, key))
        except (TypeError, ValueError, AttributeError):
            current = None  # unset or unreadable — treat as a change
        if current is None or current != value:
            changes[key] = value
    return changes


def _persist_slots(values: dict[str, Any]) -> tuple[bool, str]:
    """Persist ``[ultrawiki]`` slot keys FIRST (AP-7 atomic writer).

    Best-effort like the wiki-provider route: a read-only/locked TOML must not
    break the live apply — the caller reports ``persisted`` honestly instead.
    """
    if not values:
        return True, ""
    try:
        from jarvis.core import config_writer  # noqa: PLC0415 — lazy (AP-26)
        from jarvis.core.config import resolve_config_path  # noqa: PLC0415

        path = resolve_config_path()
        for key, value in values.items():
            config_writer.set_ultrawiki_slot(key, value, path=path)
    except Exception as exc:  # noqa: BLE001 — persist failure degrades, never 500s
        log.warning("ultrawiki slot persist failed (live apply still runs): %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _persist_enabled(enabled: bool) -> tuple[bool, str]:
    """Persist the ``[ultrawiki] enabled`` mode switch FIRST (best-effort)."""
    try:
        from jarvis.core import config_writer  # noqa: PLC0415 — lazy (AP-26)
        from jarvis.core.config import resolve_config_path  # noqa: PLC0415

        config_writer.set_ultrawiki_enabled(enabled, path=resolve_config_path())
    except Exception as exc:  # noqa: BLE001 — persist failure degrades, never 500s
        log.warning("ultrawiki enabled persist failed (live apply still runs): %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _apply_live(request: Request, values: dict[str, str], *, enabled: bool | None = None) -> None:
    """Mirror persisted values into the in-memory config (live apply)."""
    uw = _uw_cfg(request)
    if uw is None:
        return
    for key, value in values.items():
        try:
            setattr(uw, key, value)
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("in-memory ultrawiki.%s update skipped: %s", key, exc)
    if enabled is not None:
        try:
            uw.enabled = enabled
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("in-memory ultrawiki.enabled update skipped: %s", exc)


def _search_legs(cfg: Any) -> dict[str, Any]:
    """Honest per-leg availability report (keyword / vector / rerank).

    BLOCKING: the leg probes walk credentials and may touch a local endpoint.
    Callers go through :func:`_search_legs_async`.
    """
    try:
        from jarvis.ultrawiki import search as search_mod  # noqa: PLC0415 — lazy

        return search_mod.search_status(cfg)
    except Exception as exc:  # noqa: BLE001 — status must never 500
        return {"error": f"search-leg probe failed ({type(exc).__name__})"}


async def _search_legs_async(cfg: Any) -> dict[str, Any]:
    """The leg report off the event loop (which also serves voice and chat)."""
    return await asyncio.to_thread(_search_legs, cfg)


# ---------------------------------------------------------------------------
# Status + providers
# ---------------------------------------------------------------------------


@router.get("/status", summary="UltraWiki mode status")
async def get_status(request: Request) -> dict[str, Any]:
    """Honest capability, backlog, and source report — answers even when the mode is off."""
    cfg = _config(request)
    uw = getattr(cfg, "ultrawiki", None)
    enabled = bool(getattr(uw, "enabled", False))
    configured_backend = str(getattr(uw, "db_backend", "sqlite") or "sqlite")
    service = getattr(request.app.state, "ultrawiki", None)
    if service is None:
        return {
            "enabled": enabled,
            "started": False,
            "db_backend": configured_backend,
            "backend_in_use": "",
            "slots": {},
            "counts": {},
            "pipeline": {
                "running": False,
                "state": "paused",
                "reason": (
                    "UltraWiki is not wired yet — the app is still starting, or "
                    "its init failed. Nothing is being read."
                ),
                "processed": {},
            },
            "sources": [],
            "jobs": [],
            "search_legs": await _search_legs_async(cfg),
            "degradations": [
                "the UltraWiki service is not wired — the app is still "
                "starting or its init failed"
            ],
        }
    data = await service.status()
    backend = dict(data.get("backend") or {})
    slots = dict(data.get("slots") or {})
    started = bool(data.get("started"))
    slots["storage"] = {
        "configured": backend.get("configured", configured_backend),
        "in_use": backend.get("in_use", ""),
        "ready": started,
        "reason": "" if started else "the store has not been opened yet",
        "vector": data.get("vector", {}),
    }
    return {
        "enabled": bool(data.get("enabled", enabled)),
        "started": started,
        "db_backend": str(backend.get("configured") or configured_backend),
        "backend_in_use": str(backend.get("in_use") or ""),
        "slots": slots,
        "counts": data.get("counts", {}),
        "pipeline": data.get("pipeline", {}),
        "sources": data.get("sources", []),
        "jobs": data.get("jobs", []),
        "search_legs": await _search_legs_async(cfg),
        "degradations": data.get("degradations", []),
    }


@router.get("/health", summary="Is the knowledge base actually working?")
async def get_health(request: Request) -> dict[str, Any]:
    """One checklist answering "is this working, and if not, what do I click?".

    Assembled from the SAME status payload the settings screens read, so the
    checklist can never claim something the rest of the UI contradicts. It
    exists because every individual surface was already truthful while the
    whole remained unreadable: sources "approved" (permission, not import),
    a pipeline reporting "everything is processed" (of nothing), slots all
    green, and seven connected apps that no reader can pull from. Diagnosing
    that took a database query — see :mod:`jarvis.ultrawiki.health`.
    """
    from jarvis.ultrawiki import health as health_mod  # noqa: PLC0415 — lazy (AP-26)

    status = await get_status(request)

    def _candidates() -> list[dict[str, Any]]:
        # Walks the keyring + mcp.json; keep it off the event loop.
        from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415

        try:
            return plugin_bridge.list_candidates()
        except Exception:  # noqa: BLE001 — a broken registry shortens the list
            log.debug("health: candidate probe failed", exc_info=True)
            return []

    candidates = await asyncio.to_thread(_candidates)
    return health_mod.build_health(status, candidates)


@router.post(
    "/sources/sync-all",
    summary="Import every approved source that has never been read",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def sync_all_sources(
    request: Request,
    only_never_imported: bool = Query(
        default=True,
        description=(
            "Only sources that have never finished an import (the default). "
            "Pass false to re-read every approved source."
        ),
    ),
) -> dict[str, Any]:
    """The checklist's one-click fix for "approved but never imported".

    Approving a source grants permission; before auto-import on approval
    landed, it did not fetch, and installs made earlier still carry sources
    that were allowed but never read. This starts exactly those, skipping any
    already running so a double click cannot pile up duplicate work.
    """
    service = _service(request)
    status = await service.status()
    started: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for source in status.get("sources", []):
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        if source.get("consent") != "approved" or not source.get("enabled", False):
            skipped.append({"source_id": source_id, "reason": "not approved"})
            continue
        if source.get("active_job"):
            skipped.append({"source_id": source_id, "reason": "already importing"})
            continue
        if only_never_imported and source.get("last_sync_at"):
            skipped.append({"source_id": source_id, "reason": "already imported"})
            continue
        try:
            job_id = await service.start_sync(source_id)
        except Exception as exc:  # noqa: BLE001 — one refusal must not stop the rest
            skipped.append({"source_id": source_id, "reason": str(exc)[:200]})
            continue
        started.append({"source_id": source_id, "job_id": job_id})
    return {
        "started": started,
        "skipped": skipped,
        "detail": (
            f"Started {len(started)} import(s)."
            if started
            else "Nothing to import — every approved source has already been read."
        ),
    }


@router.get("/providers", summary="UltraWiki provider options per slot")
async def list_providers(request: Request) -> dict[str, Any]:
    """Option cards for the embedding, rerank, and storage slots (readiness-probed)."""
    cfg = _config(request)

    def _probe() -> dict[str, Any]:
        # Credential probes walk keyring/env/.env — keep them off the loop.
        from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415
        from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415

        embedding = embeddings_mod.available_backends(cfg)
        rerank_rows = rerank_mod.available_rerankers(cfg)
        try:
            from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy

            secret_present = bool(get_secret("ultrawiki_db_url"))
        except Exception:  # noqa: BLE001 — a broken keyring reports absent, never 500s
            secret_present = False
        return {
            "embedding": embedding,
            "rerank": rerank_rows,
            "db_backends": [
                {
                    "name": "sqlite",
                    "ready": True,
                    "reason": "",
                    "detail": (
                        "Local file under the Jarvis data directory — zero "
                        "setup, works offline on every OS."
                    ),
                },
                {
                    "name": "postgres",
                    "ready": secret_present,
                    "secret_present": secret_present,
                    "reason": (
                        ""
                        if secret_present
                        else (
                            "no 'ultrawiki_db_url' connection string is saved "
                            "— add it in the API-Keys view first"
                        )
                    ),
                    "detail": (
                        "PostgreSQL via connection string (own server, "
                        "Supabase, Neon, RDS, ...) for multi-device access."
                    ),
                },
            ],
        }

    return await asyncio.to_thread(_probe)


@router.get("/catalog", summary="UltraWiki provider catalog with credential state")
async def get_catalog(request: Request) -> dict[str, Any]:
    """Every selectable provider per slot, with its live credential + readiness state.

    This is what the settings cards render: for each of storage, embedding,
    distill and rerank it returns the declared providers
    (:mod:`jarvis.ultrawiki.provider_catalog`) enriched with the truth about
    THIS machine — which credential slots hold a value, which other Jarvis
    surfaces read the same slot (so a delete can warn before it disables
    them), and whether the provider's own probe says it is usable.

    Credential probes walk the OS keyring, so the whole enrichment runs in a
    worker thread; the route body itself never blocks the event loop.
    """
    cfg = _config(request)
    uw = getattr(cfg, "ultrawiki", None)
    selected = {
        "storage": str(getattr(uw, "storage_provider", "") or "").strip()
        or ("postgres" if str(getattr(uw, "db_backend", "") or "") == "postgres" else "sqlite"),
        "embedding": str(getattr(uw, "embedding_provider", "") or "").strip(),
        "distill": str(getattr(uw, "distill_provider", "") or "").strip(),
        "rerank": str(getattr(uw, "rerank_provider", "") or "").strip(),
    }

    def _probe() -> dict[str, Any]:
        from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy (AP-26)
        from jarvis.ui.web.provider_spec import (  # noqa: PLC0415 — lazy
            secret_slot_consumers,
        )
        from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415
        from jarvis.ultrawiki import provider_catalog  # noqa: PLC0415
        from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415

        def _secret_present(slot: str) -> bool:
            try:
                return bool(get_secret(slot))
            except Exception:  # noqa: BLE001 — a locked keyring reads as absent
                return False

        # Live readiness per slot, keyed by provider id. Each source is the
        # provider's OWN probe (AP-21: capability, never a name check).
        embedding_ready = {
            str(row.get("name")): row
            for row in embeddings_mod.available_backends(cfg)
        }
        rerank_ready = {
            str(row.get("name")): row for row in rerank_mod.available_rerankers(cfg)
        }
        try:
            from jarvis.brain.provider_registry import (  # noqa: PLC0415 — lazy
                BrainProviderRegistry,
            )
            from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy
                credential_ready_wiki_providers,
            )

            distill_chain = set(
                credential_ready_wiki_providers(
                    available=set(BrainProviderRegistry().available()), config=cfg
                )
            )
        except Exception as exc:  # noqa: BLE001 — the catalog must never 500
            log.debug("distill chain probe failed: %s", exc, exc_info=True)
            distill_chain = set()

        db_url_present = _secret_present("ultrawiki_db_url")

        def _readiness(spec: Any) -> tuple[bool, str]:
            if spec.slot == "embedding":
                row = embedding_ready.get(spec.id)
                if row is None:
                    return False, "this backend is not installed in this build"
                return bool(row.get("ready")), str(row.get("reason") or "")
            if spec.slot == "rerank":
                row = rerank_ready.get(spec.id)
                if row is None:
                    return False, "this backend is not installed in this build"
                return bool(row.get("ready")), str(row.get("reason") or "")
            if spec.slot == "distill":
                if spec.id in distill_chain:
                    return True, ""
                return False, (
                    "no usable credential for this provider — save its key "
                    "below, or leave the slot on Automatic and Jarvis uses "
                    "whichever provider you do have"
                )
            # storage
            if spec.db_backend == "sqlite":
                return True, ""
            if db_url_present:
                return True, ""
            return False, (
                "no connection string is saved yet — connect below and the "
                "local SQLite store keeps answering meanwhile"
            )

        def _row(spec: Any) -> dict[str, Any]:
            ready, reason = _readiness(spec)
            return {
                "id": spec.id,
                "slot": spec.slot,
                "label": spec.label,
                "auth_mode": spec.auth_mode,
                "secret_keys": list(spec.secret_keys),
                "dashboard_url": spec.dashboard_url,
                "credential_help": spec.credential_help,
                "default_model": spec.default_model,
                "supports_base_url": spec.supports_base_url,
                "default_base_url": spec.default_base_url,
                "recommended": spec.recommended,
                "caution": spec.caution,
                "db_backend": spec.db_backend,
                "connection_hint": spec.connection_hint,
                "ready": ready,
                "reason": reason,
                "selected": selected.get(spec.slot) == spec.id,
                "secrets_set": {
                    key: _secret_present(key) for key in spec.secret_keys
                },
                "secret_shared_with": {
                    key: secret_slot_consumers(key) for key in spec.secret_keys
                },
            }

        return {
            slot: [_row(spec) for spec in provider_catalog.catalog_for_slot(slot)]
            for slot in provider_catalog.SLOT_NAMES
        }

    slots = await asyncio.to_thread(_probe)
    return {
        "slots": slots,
        "selected": selected,
        "models": {
            "embedding": str(getattr(uw, "embedding_model", "") or ""),
            "distill": str(getattr(uw, "distill_model", "") or ""),
        },
        "ollama_endpoint": str(
            getattr(uw, "ollama_endpoint", "") or "http://localhost:11434"
        ),
    }


@router.get("/models/{slot}", summary="Selectable models for one UltraWiki slot")
async def list_slot_models(
    slot: str,
    request: Request,
    provider: str = Query(default="", description="Defaults to the slot's provider"),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """The model catalog for a slot's provider, shaped like the brain picker's.

    Same payload as ``GET /api/providers/{id}/models`` so the settings cards
    reuse the API-Keys model picker verbatim instead of a look-alike — a
    free-text box was how a one-character typo ("gemini-embedding-01") became a
    silently paused embed stage.

    Embedding models come from :mod:`jarvis.ultrawiki.embedding_models`
    (live where the provider lists them, curated otherwise); distillation and
    the chat-graded reranker are ordinary chat providers and go through the
    shared brain catalog. ``source`` stays honest either way, and the picker's
    custom-id row still reaches a model no catalog knows yet.
    """
    cfg = _config(request)
    uw = _uw_cfg(request)
    if slot not in ("embedding", "distill", "rerank"):
        raise HTTPException(
            status_code=404,
            detail="unknown slot — one of: embedding, distill, rerank",
        )

    chosen = provider.strip() or str(
        getattr(uw, f"{slot}_provider", "") or ""
    ).strip()
    current = str(getattr(uw, f"{slot}_model", "") or "").strip()
    if not chosen:
        return {
            "provider": "",
            "current_model": current,
            "models": [],
            "source": "curated",
            "fetched_at": 0.0,
            "selects": "model",
            "reason": "no provider is selected for this slot yet",
        }

    if slot == "embedding":
        from jarvis.ultrawiki import embedding_models  # noqa: PLC0415 — lazy (AP-26)

        result = await embedding_models.list_embedding_models(chosen, cfg)
        payload = result.as_dict()
        return {
            "provider": chosen,
            "current_model": current,
            "models": payload["models"],
            "source": payload["source"],
            "fetched_at": time.time(),
            "selects": "model",
            "reason": payload["reason"],
        }

    # distill + the "llm" reranker both grade with an ordinary chat provider,
    # so they share the brain catalog rather than a second copy of it.
    if slot == "rerank" and chosen != "llm":
        # A vendor cross-encoder pins its own model; there is nothing to pick.
        return {
            "provider": chosen,
            "current_model": "",
            "models": [],
            "source": "curated",
            "fetched_at": 0.0,
            "selects": "model",
            "reason": "this reranker uses its own fixed model",
        }
    if slot == "rerank":
        chosen = str(getattr(uw, "distill_provider", "") or "").strip()
        if not chosen:
            return {
                "provider": "",
                "current_model": current,
                "models": [],
                "source": "curated",
                "fetched_at": 0.0,
                "selects": "model",
                "reason": (
                    "the chat-graded reranker follows your provider chain — "
                    "pin a distillation provider to choose its model"
                ),
            }

    try:
        from jarvis.ui.web.provider_routes import (  # noqa: PLC0415 — lazy
            _get_model_catalog,
        )

        catalog = _get_model_catalog(request)
        result = await catalog.list_models(chosen, force_refresh=refresh)
    except Exception as exc:  # noqa: BLE001 — a dead catalog must not 500 the screen
        log.debug("slot model catalog failed for %s", chosen, exc_info=True)
        return {
            "provider": chosen,
            "current_model": current,
            "models": [],
            "source": "curated",
            "fetched_at": 0.0,
            "selects": "model",
            "reason": f"model list unavailable ({type(exc).__name__})",
        }
    return {
        "provider": chosen,
        "current_model": current,
        "models": [{"id": m.id, "label": m.label} for m in result.models],
        "source": result.source,
        "fetched_at": result.fetched_at,
        "selects": "model",
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Storage — the guided Supabase link
# ---------------------------------------------------------------------------


@router.get(
    "/storage/supabase/projects", summary="List the linked Supabase account's projects"
)
async def list_supabase_projects(request: Request) -> dict[str, Any]:
    """Projects visible to the saved Supabase access token (409 when unlinked)."""
    from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy (AP-26)
    from jarvis.ultrawiki import supabase_link  # noqa: PLC0415 — lazy

    token = await asyncio.to_thread(get_secret, "supabase_access_token")
    if not token:
        raise HTTPException(
            status_code=409,
            detail=(
                "No Supabase access token is saved. Open the Supabase tokens "
                "page from the storage card, create a token, and paste it in."
            ),
        )
    try:
        projects = await supabase_link.list_projects(token)
    except supabase_link.SupabaseLinkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "projects": [p.as_dict() for p in projects],
        "total": len(projects),
        "tokens_url": supabase_link.SUPABASE_TOKENS_URL,
    }


class SupabaseLinkBody(BaseModel):
    """Finish the Supabase link: which project, and its database password."""

    project_ref: str = Field(min_length=1)
    db_password: str = Field(min_length=1)
    #: "transaction" (the default, right for a long-lived app pool) or "session".
    pool_mode: str = "transaction"
    #: Save even when the connection probe fails. Off by default — a string
    #: that cannot connect is normally a mistake worth catching here rather
    #: than as a silent SQLite fallback three restarts later. On for the user
    #: who knows their network blocks the probe (VPN-only database, firewall).
    save_anyway: bool = False


@router.post(
    "/storage/supabase/link",
    summary="Link a Supabase project as the UltraWiki store",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def link_supabase_project(
    body: SupabaseLinkBody, request: Request
) -> dict[str, Any]:
    """Assemble, probe and save the Supabase connection string.

    Reads the project's real pooler host from Supabase (guessing the region
    prefix would produce a string that fails for invisible reasons), adds the
    password the user supplied, probes the connection, and only then writes the
    credential and flips the storage slot to Postgres. A failing probe answers
    409 with the sanitized reason and saves nothing unless ``save_anyway``.
    """
    from jarvis.core.config import get_secret, set_secret  # noqa: PLC0415 — lazy
    from jarvis.ultrawiki import supabase_link  # noqa: PLC0415 — lazy

    token = await asyncio.to_thread(get_secret, "supabase_access_token")
    if not token:
        raise HTTPException(
            status_code=409, detail="No Supabase access token is saved."
        )
    try:
        endpoint, note = await supabase_link.resolve_endpoint(
            token, body.project_ref.strip(), mode=body.pool_mode
        )
        conn_str = supabase_link.build_connection_string(endpoint, body.db_password)
    except supabase_link.SupabaseLinkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    from jarvis.ultrawiki.store import PostgresStore  # noqa: PLC0415 — lazy

    probe_ok, probe_detail = await PostgresStore.connect_test(conn_str)
    if not probe_ok and not body.save_anyway:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"{note} The connection could not be established, so "
                    f"nothing was saved: {probe_detail}"
                ),
                "probe_detail": probe_detail,
                "endpoint": endpoint.as_dict(),
                "can_save_anyway": True,
            },
        )

    if not await asyncio.to_thread(set_secret, "ultrawiki_db_url", conn_str):
        raise HTTPException(
            status_code=500,
            detail=(
                "The connection string could not be stored in this machine's "
                "credential store."
            ),
        )
    values = {"db_backend": "postgres", "storage_provider": "supabase"}
    persisted, persist_error = _persist_slots(values)
    _apply_live(request, values)
    response: dict[str, Any] = {
        "ok": True,
        "project_ref": body.project_ref.strip(),
        "endpoint": endpoint.as_dict(),
        "note": note,
        "probe_ok": probe_ok,
        "probe_detail": probe_detail,
        "persisted": persisted,
        "restart_required": True,
        "detail": (
            "Supabase is linked. The store switches over on the next app "
            "restart; until then the current store keeps answering and "
            "nothing is lost."
        ),
    }
    if persist_error:
        response["persist_error"] = persist_error
    return response


# ---------------------------------------------------------------------------
# Activation / deactivation / settings
# ---------------------------------------------------------------------------


class ActivateBody(BaseModel):
    """Activation payload — the deliberate one-time capability-slot choices."""

    db_backend: str = ""  # "" keeps the configured value ("sqlite" default)
    embedding_provider: str = Field(min_length=1)
    embedding_model: str = ""
    distill_provider: str = ""
    distill_model: str = ""
    rerank_provider: str = ""
    rerank_model: str = ""
    areas: list[str] = Field(default_factory=list)


@router.post(
    "/activate",
    summary="Activate UltraWiki mode",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def activate_mode(body: ActivateBody, request: Request) -> dict[str, Any]:
    """Turn UltraWiki mode on: persist the slot choices, then register the
    default sources (consent pending — nothing is pulled)."""
    service = _service(request)
    cfg = _config(request)
    from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415

    provider = body.embedding_provider.strip()
    rows = await asyncio.to_thread(embeddings_mod.available_backends, cfg)
    row = next((r for r in rows if r.get("name") == provider), None)
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown embedding provider {provider!r} "
                f"(available: {sorted(str(r.get('name')) for r in rows)})"
            ),
        )
    if not row.get("ready"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"embedding provider {provider!r} is not ready: "
                f"{row.get('reason') or 'unknown reason'}"
            ),
        )
    db_backend = body.db_backend.strip().lower()
    if db_backend and db_backend not in ("sqlite", "postgres"):
        raise HTTPException(
            status_code=400, detail="db_backend must be 'sqlite' or 'postgres'"
        )
    rerank_provider = body.rerank_provider.strip()
    if rerank_provider:
        from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415

        if rerank_provider not in rerank_mod.RERANK_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown rerank provider {rerank_provider!r} "
                    f"(available: {sorted(rerank_mod.RERANK_BACKENDS)})"
                ),
            )

    values: dict[str, str] = {"embedding_provider": provider}
    if db_backend:
        values["db_backend"] = db_backend
    for key in ("embedding_model", "distill_provider", "distill_model", "rerank_model"):
        value = str(getattr(body, key) or "").strip()
        if value:
            values[key] = value
    if rerank_provider:
        values["rerank_provider"] = rerank_provider

    # Ordering matters. The slot values go to disk and into the live config
    # FIRST (the disk is the source of truth — the PUT
    # /api/settings/wiki-provider discipline), because the store must open
    # against the chosen backend. The MODE SWITCH is flipped LAST, only after
    # the one step that can actually fail — activate() opens the store and
    # seeds areas + sources. Enabling first meant a failed activation left the
    # mode ON with no store behind it: the whole Wiki section switched to a
    # broken Ultra view the user could not get out of.
    slots_persisted, slots_error = _persist_slots(values)
    _apply_live(request, values)

    try:
        result = await service.activate({"areas": list(body.areas or [])})
    except Exception as exc:  # noqa: BLE001 — the mode stays OFF and untouched
        log.exception("UltraWiki activation failed; the mode stays off")
        raise HTTPException(
            status_code=500,
            detail=(
                "UltraWiki could not be activated "
                f"({type(exc).__name__}: {exc}). The mode stays off and the "
                "normal wiki keeps answering; your slot choices were saved."
            ),
        ) from exc

    enabled_persisted, enabled_error = _persist_enabled(True)
    _apply_live(request, {}, enabled=True)
    # The pipeline only starts while the mode is on, so it is started here —
    # after the flip, never before it.
    await service.ensure_started()

    response: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "persisted": slots_persisted and enabled_persisted,
        **result,
        "next_steps": (
            "UltraWiki is on, but nothing has been read yet: open the "
            "sources list, approve each source you want ingested, then start "
            "a sync. Keyword search works seconds after the first sync; "
            "semantic answers grow as the background pipeline embeds and "
            "distills."
        ),
    }
    persist_error = slots_error or enabled_error
    if persist_error:
        response["persist_error"] = persist_error
    return response


@router.post(
    "/deactivate",
    summary="Deactivate UltraWiki mode",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def deactivate_mode(request: Request) -> dict[str, Any]:
    """Turn UltraWiki mode off (non-destructive) — the normal wiki answers again."""
    service = _service(request)
    persisted, persist_error = _persist_enabled(False)
    _apply_live(request, {}, enabled=False)
    try:
        # Stops the pipeline + sync tasks and closes the store; the data stays
        # on disk untouched. A later activation reopens it where it left off.
        await service.shutdown()
    except Exception as exc:  # noqa: BLE001 — teardown best-effort
        log.warning("UltraWiki shutdown during deactivate failed: %s", exc)
    response: dict[str, Any] = {
        "ok": True,
        "enabled": False,
        "persisted": persisted,
        "non_destructive": True,
        "detail": (
            "UltraWiki mode is off. Nothing was deleted — every ingested "
            "item, embedding, and source stays on disk, and re-activating "
            "picks up exactly where you left off. The normal wiki answers "
            "again."
        ),
    }
    if persist_error:
        response["persist_error"] = persist_error
    return response


class UpdateSettingsBody(BaseModel):
    """Slot changes; an embedding change needs confirm_reembed once vectors exist."""

    db_backend: str | None = None
    #: The named storage preset (sqlite / supabase / neon / postgres). When it
    #: is sent without an explicit ``db_backend``, the functional backend is
    #: derived from the preset — the UI picks a NAME, never an internal enum.
    storage_provider: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    distill_provider: str | None = None
    distill_model: str | None = None
    rerank_provider: str | None = None
    #: Only meaningful for rerank_provider="llm" — empty lets the provider
    #: chain pick each family's cheap router-tier model.
    rerank_model: str | None = None
    ollama_endpoint: str | None = None
    #: Ranking knobs of the read path. Absolute 0-10 relevance floor for
    #: unsolicited surfaces, per-leg fusion weights, and the age-decay half
    #: life in days (0 = no decay).
    rerank_min_score: float | None = None
    rrf_keyword_weight: float | None = None
    rrf_vector_weight: float | None = None
    recency_half_life_days: float | None = None
    confirm_reembed: bool = False


@router.put(
    "/settings",
    summary="Change UltraWiki slot settings",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    """Change capability-slot settings; an embedding change drops and
    re-embeds the corpus after explicit confirmation."""
    service = _service(request)
    uw = _uw_cfg(request)

    incoming: dict[str, str] = {
        key: str(getattr(body, key)).strip()
        for key in _SLOT_KEYS
        if getattr(body, key) is not None
    }
    # A storage preset is a NAME the user picked; the functional two-value
    # backend is derived from it so the UI never has to know the internal enum
    # (and cannot desync from it). An explicit db_backend still wins.
    preset = incoming.get("storage_provider")
    if preset:
        from jarvis.ultrawiki import provider_catalog  # noqa: PLC0415 — lazy (AP-26)

        if provider_catalog.get_provider_spec("storage", preset) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown storage provider {preset!r} (available: "
                    f"{[s.id for s in provider_catalog.STORAGE_PROVIDERS]})"
                ),
            )
        incoming.setdefault("db_backend", provider_catalog.storage_backend_of(preset))

    changes = {
        key: value
        for key, value in incoming.items()
        if value != str(getattr(uw, key, "") or "").strip()
    }
    ranking_changes = _ranking_changes(body, uw)
    if not changes and not ranking_changes:
        return {"ok": True, "changed": [], "persisted": True, "reembed_started": False}

    if "db_backend" in changes and changes["db_backend"] not in ("sqlite", "postgres"):
        raise HTTPException(
            status_code=400, detail="db_backend must be 'sqlite' or 'postgres'"
        )
    if changes.get("embedding_provider"):
        from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415

        if changes["embedding_provider"] not in embeddings_mod.EMBEDDING_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown embedding provider {changes['embedding_provider']!r} "
                    f"(available: {sorted(embeddings_mod.EMBEDDING_BACKENDS)})"
                ),
            )
    if changes.get("rerank_provider"):
        from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415

        if changes["rerank_provider"] not in rerank_mod.RERANK_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown rerank provider {changes['rerank_provider']!r} "
                    f"(available: {sorted(rerank_mod.RERANK_BACKENDS)})"
                ),
            )

    embedding_change = any(
        key in changes for key in ("embedding_provider", "embedding_model")
    )
    vector_items = 0
    if embedding_change:
        store = await _store_of(service)
        counts = await store.counts()
        vector_items = int(counts.embedded) + int(counts.distilled)
        if vector_items and not body.confirm_reembed:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "changing the embedding provider or model switches "
                        f"vector spaces: the {vector_items} already-embedded "
                        "items must be re-embedded from scratch. Repeat the "
                        "request with confirm_reembed=true to drop the "
                        "existing vectors and re-embed in the background — "
                        "keyword search keeps working meanwhile."
                    ),
                    "vector_items": vector_items,
                },
            )

    applied: dict[str, Any] = {**changes, **ranking_changes}
    persisted, persist_error = _persist_slots(applied)
    _apply_live(request, applied)
    reembed_started = False
    if embedding_change:
        store = await _store_of(service)
        # Drops uw_vec + uw_embeddings, clears the model/dim pin, and resets
        # embedded/distilled items to keyword_indexed — the running pipeline
        # re-embeds them in the background with the new slot.
        await store.reset_vectors()
        reembed_started = vector_items > 0
    response: dict[str, Any] = {
        "ok": True,
        "changed": sorted(applied),
        "persisted": persisted,
        "reembed_started": reembed_started,
    }
    if persist_error:
        response["persist_error"] = persist_error
    return response


@router.post(
    "/test/{slot}",
    summary="Test one UltraWiki capability slot",
    openapi_extra={
        "x-jarvis-dangerous": True,
        # A real provider call: the embedding path allows 120 s read timeout
        # (a cold local Ollama model load is slow), and distillation is a full
        # LLM round trip. The CLI's default client timeout would give up long
        # before the slot does and report a failure that never happened.
        "x-jarvis-timeout-seconds": 120,
    },
)
async def test_slot(slot: str, request: Request) -> dict[str, Any]:
    """Run one real minimal call against a slot (embedding, distill, rerank, or storage)."""
    cfg = _config(request)
    started = time.perf_counter()

    def _result(ok: bool, detail: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "detail": detail,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }

    if slot == "embedding":
        provider = str(getattr(_uw_cfg(request), "embedding_provider", "") or "").strip()
        if not provider:
            return _result(False, "no embedding provider is configured")
        from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415

        factory = embeddings_mod.EMBEDDING_BACKENDS.get(provider)
        if factory is None:
            return _result(False, f"unknown embedding provider {provider!r}")
        model = str(getattr(_uw_cfg(request), "embedding_model", "") or "").strip()
        model = model or embeddings_mod.DEFAULT_MODELS.get(provider, "")
        try:
            vectors = await factory(cfg).embed(["Jarvis connectivity test"], model=model)
        except Exception as exc:  # noqa: BLE001 — a test reports, never 500s
            return _result(False, f"{type(exc).__name__}: {exc}")
        if not vectors or not vectors[0]:
            return _result(False, f"{provider} returned no vector")
        return _result(
            True,
            f"embedded one test text with {provider}/{model} "
            f"({len(vectors[0])} dimensions)",
        )

    if slot == "distill":
        from jarvis.ultrawiki.distill import distill_text  # noqa: PLC0415

        try:
            result = await distill_text(
                cfg,
                title="Connectivity test",
                body="The user is checking that distillation works end to end.",
                source_kind="test",
            )
        except Exception as exc:  # noqa: BLE001 — a test reports, never 500s
            return _result(False, f"{type(exc).__name__}: {exc}")
        summary = str(getattr(result, "summary", "") or "").strip()
        return _result(True, f"distilled a test snippet ({summary[:80] or 'empty summary'})")

    if slot == "rerank":
        from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415

        reranker = rerank_mod.resolve_reranker(cfg)
        if reranker is None:
            return _result(
                False,
                "rerank is not configured or not ready — the fusion order "
                "stands (optional stage)",
            )
        # A real grading call over two obviously-unequal documents: it proves
        # the provider answers AND that the 0-10 scale arrives, which is what
        # the relevance floor depends on.
        try:
            pairs = await reranker.rerank(
                "what is the invoice total?",
                [
                    "The invoice total is 1,240 EUR, due on the 30th.",
                    "sounds good, thanks!",
                ],
                top_k=2,
            )
        except Exception as exc:  # noqa: BLE001 — a test reports, never 500s
            return _result(False, f"{type(exc).__name__}: {exc}")
        if not pairs:
            return _result(False, f"{reranker.name} graded no document")
        best_index, best_score = pairs[0]
        return _result(
            True,
            f"{reranker.name} graded 2 documents; best is #{best_index} "
            f"at {best_score:.1f}/10",
        )

    if slot == "storage":
        service = _service(request)
        backend = str(getattr(_uw_cfg(request), "db_backend", "sqlite") or "sqlite")
        if backend.strip().lower() == "postgres":
            try:
                from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy

                conn_str = await asyncio.to_thread(get_secret, "ultrawiki_db_url")
            except Exception:  # noqa: BLE001 — a broken keyring reads as absent
                conn_str = None
            if not conn_str:
                return _result(
                    False,
                    "no 'ultrawiki_db_url' connection string is saved — add "
                    "it in the API-Keys view",
                )
            from jarvis.ultrawiki.store import PostgresStore  # noqa: PLC0415

            ok, reason = await PostgresStore.connect_test(conn_str)
            return _result(bool(ok), reason or "connected to Postgres")
        try:
            store = await _store_of(service)
            vec_ok, vec_reason = await store.vector_status()
        except HTTPException as exc:
            return _result(False, str(exc.detail))
        except Exception as exc:  # noqa: BLE001 — a test reports, never 500s
            return _result(False, f"{type(exc).__name__}: {exc}")
        vector_note = "vector search ready" if vec_ok else f"vector search off ({vec_reason})"
        return _result(True, f"SQLite store open; {vector_note}")

    raise HTTPException(
        status_code=404,
        detail="unknown slot — one of: embedding, distill, rerank, storage",
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@router.get("/sources", summary="List UltraWiki sources")
async def list_sources(request: Request) -> dict[str, Any]:
    """Configured sources with consent, per-stage counts, and sync state."""
    service = _service(request)
    status = await service.status()
    sources = status.get("sources", [])
    return {"sources": sources, "total": len(sources)}


class CreateSourceBody(BaseModel):
    """New source registration — consent starts PENDING; nothing is pulled."""

    connector: str = Field(min_length=1)
    label: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    areas: list[str] = Field(default_factory=list)


@router.post("/sources", status_code=201, summary="Register an UltraWiki source")
async def create_source(body: CreateSourceBody, request: Request) -> dict[str, Any]:
    """Register a new source with consent PENDING — approval is a separate explicit step."""
    service = _service(request)
    try:
        source = await service.add_source(
            body.connector.strip(),
            body.label.strip(),
            config=dict(body.config or {}),
            area_ids=list(body.areas or []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return source


@router.post(
    "/sources/{source_id}/approve",
    summary="Approve an UltraWiki source and import everything it holds",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def approve_source(
    source_id: str,
    request: Request,
    auto_sync: bool = Query(
        default=True,
        description=(
            "Start the full import immediately (the default). Pass false to "
            "grant consent only and sync later."
        ),
    ),
) -> dict[str, Any]:
    """Grant consent for one source — THE gate before any byte is pulled.

    Approval is what the consent contract asks for, so it also STARTS the full
    import: the answer carries the ``job_id`` of the run that is now pulling
    everything the source holds (``null`` when none could be started, with
    ``detail`` saying why). A refused import never fails the approval.
    """
    service = _service(request)
    try:
        return await service.approve_source(source_id, auto_sync=bool(auto_sync))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sources/{source_id}/revoke", summary="Revoke an UltraWiki source")
async def revoke_source(source_id: str, request: Request) -> dict[str, Any]:
    """Revoke consent for one source; future syncs refuse until re-approved."""
    service = _service(request)
    try:
        return await service.revoke_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/sources/{source_id}", summary="Delete an UltraWiki source")
async def delete_source(
    source_id: str,
    request: Request,
    purge: bool = Query(
        default=False,
        description="Also delete the source's ingested items and derived data",
    ),
) -> dict[str, Any]:
    """Remove a source registration; purge=true also deletes its ingested data."""
    service = _service(request)
    store = await _store_of(service)
    if await store.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    await store.delete_source(source_id, purge=bool(purge))
    return {"ok": True, "deleted": source_id, "purged": bool(purge)}


class SyncBody(BaseModel):
    """Sync options. ``full`` is the FULL REFRESH: it clears the resume
    checkpoint and the incremental cursor so the connector re-reads everything
    from scratch — the only mode in which deleted items can be detected and
    tombstoned (a delete is invisible to an incremental run)."""

    full: bool = False


@router.post(
    "/sources/{source_id}/sync",
    status_code=201,
    summary="Start a sync for one UltraWiki source",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def start_sync(
    source_id: str, request: Request, body: SyncBody | None = None
) -> dict[str, Any]:
    """Start a sync job for one approved source; full=true re-reads everything."""
    service = _service(request)
    full = bool(body.full) if body is not None else False
    from jarvis.ultrawiki.service import (  # noqa: PLC0415 — lazy (AP-26)
        SyncAlreadyRunningError,
    )

    try:
        job_id = await service.start_sync(source_id, full=full)
    except SyncAlreadyRunningError as exc:
        # 409 with the ACTIVE job id, so a caller can watch or cancel it
        # instead of piling a second interleaving sync onto the same source.
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "source_id": exc.source_id,
                "job_id": exc.job_id,
            },
        ) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("unknown source") else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {
        "job_id": job_id,
        "status": "queued",
        "source_id": source_id,
        "full": full,
    }


@router.get("/connectors", summary="List every UltraWiki source connector offered")
async def list_connectors() -> dict[str, Any]:
    """The curated roster the add-source picker renders: built-ins and integrations.

    Static product data — the catalog, not the install. It says what UltraWiki
    OFFERS (name, brand mark, whether a reader exists yet); which of them this
    machine has actually connected is the separate ``/bridge/candidates`` call.
    """
    from jarvis.ultrawiki import connector_catalog  # noqa: PLC0415 — lazy (AP-26)

    connectors = [
        connector_catalog.as_dict(spec) for spec in connector_catalog.list_connectors()
    ]
    return {
        "connectors": connectors,
        "total": len(connectors),
        "builtin": sum(1 for row in connectors if row["kind"] == "builtin"),
        "bridge": sum(1 for row in connectors if row["kind"] == "bridge"),
    }


@router.get("/bridge/candidates", summary="List plugin-bridge candidates")
async def list_bridge_candidates() -> dict[str, Any]:
    """Curated integrations for the picker, each flagged connected or not.

    Only roster entries appear — a connected tool UltraWiki does not curate is
    left out entirely, because offering it would promise a reader that will
    never exist. A curated integration the user has NOT connected is included
    with ``connected: false`` so the picker can show what is possible and point
    at the Plugins store, rather than pretending the roster is empty.
    """
    from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415 — lazy

    # Discovery walks the OS keyring and reads mcp.json — blocking work that
    # belongs in a worker thread, the same way the health route runs it.
    candidates = await asyncio.to_thread(plugin_bridge.list_offered_integrations)
    connected = sum(1 for row in candidates if row.get("connected"))
    return {
        "candidates": candidates,
        "total": len(candidates),
        "connected": connected,
    }


# ---------------------------------------------------------------------------
# Pipeline recovery
# ---------------------------------------------------------------------------


class RequeueFailedBody(BaseModel):
    """Scope of a requeue; ``source_id`` empty means every source."""

    source_id: str = ""


@router.post(
    "/pipeline/requeue-failed",
    summary="Retry UltraWiki items that gave up",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def requeue_failed(
    request: Request, body: RequeueFailedBody | None = None
) -> dict[str, Any]:
    """Return dead-lettered items to the pipeline (they retry from their last completed stage)."""
    service = _service(request)
    source_id = (body.source_id.strip() if body is not None else "") or None
    try:
        moved = await service.requeue_failed(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "requeued": moved,
        "source_id": source_id or "",
        "detail": (
            f"{moved} item(s) will be picked up again from their last completed "
            "stage. Items whose cause is still broken will simply pause there."
            if moved
            else "No item was in the failed state — nothing to requeue."
        ),
    }


# ---------------------------------------------------------------------------
# Sync jobs
# ---------------------------------------------------------------------------


@router.get("/jobs", summary="List UltraWiki sync jobs")
async def list_jobs(
    request: Request, limit: int = Query(default=20, ge=1, le=100)
) -> dict[str, Any]:
    """Newest-first sync-job snapshots (active and recent terminal jobs)."""
    service = _service(request)
    jobs = service.list_jobs(limit)
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/jobs/{job_id}", summary="Inspect one UltraWiki sync job")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    """One sync job's snapshot (404 when unknown)."""
    snapshot = _service(request).job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    return snapshot


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel one UltraWiki sync job",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    """Cancel one active sync job (404 unknown, 409 already finished)."""
    service = _service(request)
    snapshot = service.job_snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    from jarvis.ultrawiki.service import JOB_TERMINAL_STATUSES  # noqa: PLC0415

    if snapshot.get("status") in JOB_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is already terminal ({snapshot.get('status')})",
        )
    if not service.cancel_job(job_id):
        raise HTTPException(
            status_code=409,
            detail="job has no live task (it is about to start or end)",
        )
    return {"job_id": job_id, "cancel_requested": True}


# ---------------------------------------------------------------------------
# Stored contents — the inventory of what is actually in the database
# ---------------------------------------------------------------------------


class UltraWikiItemRow(BaseModel):
    """One stored item as the inventory view lists it."""

    id: int
    source_id: str
    #: The item's title, or its external id when the connector supplied none —
    #: never an empty line the user cannot identify.
    title: str
    state: str
    permalink: str
    #: When the item was created AT THE SOURCE.
    timestamp_utc: str
    #: When UltraWiki first stored it (drives the newest-first order).
    ingested_at: str
    updated_at: str


class UltraWikiItemsPage(BaseModel):
    """One page of the inventory plus the unpaged total."""

    items: list[UltraWikiItemRow]
    total: int
    limit: int
    offset: int


@router.get("/items", summary="List the items stored in the UltraWiki database")
async def list_items(
    request: Request,
    source_id: str = Query(default="", description="Only items of this source"),
    state: str = Query(
        default="", description="Only items in this pipeline state"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(
        default=False,
        description="Also list items that were tombstoned after a full refresh",
    ),
) -> UltraWikiItemsPage:
    """Newest-first inventory of what UltraWiki actually holds, with filters.

    The counts elsewhere say HOW MANY items exist per stage; this says WHICH
    ones — the question a user asking "what is even in this database?" is
    actually asking. Tombstoned rows stay out unless asked for: they no longer
    take part in answers.
    """
    service = _service(request)
    store = await _store_of(service)
    wanted_state = state.strip()
    if wanted_state:
        from jarvis.ultrawiki.types import ItemState  # noqa: PLC0415 — lazy (AP-26)

        if wanted_state not in {member.value for member in ItemState}:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown state {wanted_state!r} (one of: "
                    f"{', '.join(member.value for member in ItemState)})"
                ),
            )
    rows, total = await store.list_items(
        source_id=source_id.strip() or None,
        state=wanted_state or None,
        limit=limit,
        offset=offset,
        include_deleted=bool(include_deleted),
    )
    return UltraWikiItemsPage(
        items=[UltraWikiItemRow(**row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


@router.get("/areas", summary="List UltraWiki areas")
async def list_areas(request: Request) -> dict[str, Any]:
    """Named source bundles (areas) used to scope sources and search."""
    store = await _store_of(_service(request))
    areas = await store.list_areas()
    return {"areas": areas, "total": len(areas)}


class CreateAreaBody(BaseModel):
    """New area; the id is derived from the name unless given explicitly."""

    name: str = Field(min_length=1)
    id: str = ""


@router.post("/areas", status_code=201, summary="Create an UltraWiki area")
async def create_area(body: CreateAreaBody, request: Request) -> dict[str, Any]:
    """Create (or rename) an area — an idempotent upsert on the area id."""
    store = await _store_of(_service(request))
    name = body.name.strip()
    area_id = body.id.strip() or _slugify(name)
    await store.upsert_area(area_id, name)
    return {"id": area_id, "name": name}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/search", summary="Hybrid search over the UltraWiki store")
async def search_ultrawiki(
    request: Request,
    q: str = Query(..., min_length=1, description="The search query"),
    k: int = Query(default=10, ge=1, le=50),
    area: str | None = Query(default=None, description="Optional area id filter"),
) -> dict[str, Any]:
    """Fused keyword + vector search, best hits first, each with its citation permalink."""
    service = _require_active(request)
    # The service owns the retrieval delegation (jarvis.ultrawiki.search).
    # An AttributeError raised INSIDE the search path must surface as the bug
    # it is, not be swallowed by a compatibility fallback for a wrapper that
    # has long since landed.
    results = await service.search(q, k=k, area_id=area)
    rows = [
        dataclasses.asdict(hit) if dataclasses.is_dataclass(hit) else dict(hit)
        for hit in results
    ]
    return {"query": q, "results": rows, "total": len(rows)}
