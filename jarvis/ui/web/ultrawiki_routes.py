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
    "embedding_provider",
    "embedding_model",
    "distill_provider",
    "distill_model",
    "rerank_provider",
)

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


def _persist_slots(values: dict[str, str]) -> tuple[bool, str]:
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
    """Honest per-leg availability report (keyword / vector / rerank)."""
    try:
        from jarvis.ultrawiki import search as search_mod  # noqa: PLC0415 — lazy

        return search_mod.search_status(cfg)
    except Exception as exc:  # noqa: BLE001 — status must never 500
        return {"error": f"search-leg probe failed ({type(exc).__name__})"}


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
            "pipeline": {"running": False, "processed": {}},
            "sources": [],
            "jobs": [],
            "search_legs": _search_legs(cfg),
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
        "search_legs": _search_legs(cfg),
        "degradations": data.get("degradations", []),
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
    for key in ("embedding_model", "distill_provider", "distill_model"):
        value = str(getattr(body, key) or "").strip()
        if value:
            values[key] = value
    if rerank_provider:
        values["rerank_provider"] = rerank_provider

    # Persist FIRST (the disk is the source of truth), then live-apply — the
    # same discipline as PUT /api/settings/wiki-provider.
    slots_persisted, slots_error = _persist_slots(values)
    enabled_persisted, enabled_error = _persist_enabled(True)
    _apply_live(request, values, enabled=True)

    result = await service.activate({"areas": list(body.areas or [])})
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
    embedding_provider: str | None = None
    embedding_model: str | None = None
    distill_provider: str | None = None
    distill_model: str | None = None
    rerank_provider: str | None = None
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
    changes: dict[str, str] = {}
    for key in _SLOT_KEYS:
        value = getattr(body, key)
        if value is None:
            continue
        value = str(value).strip()
        if value != str(getattr(uw, key, "") or "").strip():
            changes[key] = value
    if not changes:
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

    persisted, persist_error = _persist_slots(changes)
    _apply_live(request, changes)
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
        "changed": sorted(changes),
        "persisted": persisted,
        "reembed_started": reembed_started,
    }
    if persist_error:
        response["persist_error"] = persist_error
    return response


@router.post(
    "/test/{slot}",
    summary="Test one UltraWiki capability slot",
    openapi_extra={"x-jarvis-dangerous": True},
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
        try:
            await reranker.rerank(
                "test query",
                ["first test document", "second test document"],
                top_k=1,
            )
        except Exception as exc:  # noqa: BLE001 — a test reports, never 500s
            return _result(False, f"{type(exc).__name__}: {exc}")
        return _result(True, f"reranked two test documents with {reranker.name}")

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
    summary="Approve an UltraWiki source",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def approve_source(source_id: str, request: Request) -> dict[str, Any]:
    """Grant consent for one source — THE gate before any byte is pulled."""
    service = _service(request)
    try:
        return await service.approve_source(source_id)
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


@router.post(
    "/sources/{source_id}/sync",
    status_code=201,
    summary="Start a sync for one UltraWiki source",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def start_sync(source_id: str, request: Request) -> dict[str, Any]:
    """Start a backfill/incremental sync job for one approved source (201 with the job id)."""
    service = _service(request)
    try:
        job_id = await service.start_sync(source_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("unknown source") else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"job_id": job_id, "status": "queued", "source_id": source_id}


@router.get("/bridge/candidates", summary="List plugin-bridge candidates")
async def list_bridge_candidates() -> dict[str, Any]:
    """Connected Jarvis integrations (plugins, MCP servers) that could become sources."""
    from jarvis.ultrawiki.connectors import plugin_bridge  # noqa: PLC0415 — lazy

    candidates = plugin_bridge.list_candidates()
    return {"candidates": candidates, "total": len(candidates)}


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
    try:
        results = await service.search(q, k=k, area_id=area)
    except AttributeError:
        # The service delegates to jarvis.ultrawiki.search.search(); until
        # that wrapper lands this route calls the hybrid entry point directly.
        from jarvis.ultrawiki import search as search_mod  # noqa: PLC0415

        store = await _store_of(service)
        results = await search_mod.hybrid_search(
            store, _config(request), q, k=k, area_id=area
        )
    rows = [
        dataclasses.asdict(hit) if dataclasses.is_dataclass(hit) else dict(hit)
        for hit in results
    ]
    return {"query": q, "results": rows, "total": len(rows)}
