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
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

# Pure arithmetic over the status counts, no store or model behind it - cheap
# enough for module level, unlike the store/embeddings/search imports below.
from jarvis.ultrawiki.progress import build_progress

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
    "rrf_event_weight",
    "recency_half_life_days",
)

#: Inclusive bounds per ranking knob.
_RANKING_BOUNDS: dict[str, tuple[float, float]] = {
    "rerank_min_score": (0.0, 10.0),  # the shared 0-10 relevance scale
    "rrf_keyword_weight": (0.0, 10.0),
    "rrf_vector_weight": (0.0, 10.0),
    "rrf_event_weight": (0.0, 10.0),
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


def _is_configured(uw: Any) -> bool:
    """Has the one-time activation wizard ever been completed on this install?

    The single question the Normal/Ultra switch needs before deciding between
    "re-activate with the stored choices" and "walk the user through the
    wizard". It is answered from the CONFIG alone — the embedding slot is the
    one choice activation cannot proceed without, and once written it survives
    every restart.

    Deliberately NOT derived from the live slot report: that one also probes
    credentials and only exists once the service is wired, so during boot it
    is empty. A client reading emptiness as "never configured" is exactly how
    a restart reopened the one-time wizard on an install that had been running
    Ultra for weeks, and offered to re-embed a corpus it had already embedded.
    Configured and ready are different questions; this answers only the first.
    """
    return bool(str(getattr(uw, "embedding_provider", "") or "").strip())


def _slots_from_config(uw: Any) -> dict[str, Any]:
    """The stored slot choices, without asking the (not yet wired) service.

    Same shape as the live report minus everything that needs a probe: no
    ``ready``/``reason``/``available``, because a booting app genuinely does
    not know those yet and inventing them would be the dishonesty this whole
    payload exists to avoid.
    """

    def slot(provider_key: str, model_key: str) -> dict[str, Any]:
        return {
            "provider": str(getattr(uw, provider_key, "") or "").strip(),
            "model": str(getattr(uw, model_key, "") or "").strip(),
            "ready": False,
            "reason": "the UltraWiki service has not started yet",
        }

    return {
        "embedding": slot("embedding_provider", "embedding_model"),
        "distill": slot("distill_provider", "distill_model"),
        "rerank": slot("rerank_provider", "rerank_model"),
        "storage": {
            "configured": str(getattr(uw, "db_backend", "sqlite") or "sqlite"),
            "in_use": "",
            "ready": False,
            "reason": "the store has not been opened yet",
            "vector": {},
        },
    }


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


def _apply_reembed_to_legs(
    legs: dict[str, Any],
    reembed: dict[str, Any],
    throughput: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark the vector leg unavailable while a model switch is rebuilding.

    The leg probe is a CREDENTIAL check by contract (AP-21): it answers "is
    this backend reachable", which stays true throughout a rebuild. But the
    ANN index mirrors the space the store is still pinned to, while the query
    is embedded with the NEW model — so every semantic query is refused on a
    dimension mismatch and search silently falls back to keyword hits alone.
    Reporting the leg as available anyway is how the health screen came to say
    "Both exact words and meaning are searchable" during an outage that lasted
    as long as the rebuild.

    The counter says WHICH population it counts, which the bare "2 592 of
    4 712" did not. Read beside an overview reporting 235 915 items queued,
    that number was taken for the whole job and made the rebuild look nearly
    done; it only ever covered the items that could already be searched by
    meaning before the switch. Everything imported since queues behind it, so
    the estimate carries the whole embedding backlog, not the rebuild's slice.
    """
    model = str(reembed.get("model") or "")
    if not model:
        return legs
    vector = dict(legs.get("vector") or {})
    if not vector or vector.get("available") is False:
        return legs
    done = int(reembed.get("done") or 0)
    total = int(reembed.get("total") or 0)
    if total:
        scope = (
            f"{done} of the {total} item(s) that could already be searched by "
            "meaning have been re-measured"
        )
    else:
        scope = "it has just started"
    eta = _embed_eta_phrase(throughput)
    return {
        **legs,
        "vector": {
            **vector,
            "available": False,
            "rebuilding": True,
            # Deliberately short: `health.py::_search_check` appends what
            # keyword-only costs, and two sentences saying the same thing read
            # as two different problems.
            "reason": (
                f"semantic search is rebuilding on {model} — {scope}. Anything "
                "imported since queues behind it" + eta + "."
            ),
        },
    }


def _embed_eta_phrase(throughput: dict[str, Any] | None) -> str:
    """``", and the whole queue needs about 4 days at the current rate"``, or ``""``.

    Empty whenever the rate is not yet measurable or the lane is standing
    still — an un-timed sentence is better than an invented duration, which is
    the failure this whole payload exists to correct.
    """
    from jarvis.ultrawiki.throughput import format_duration  # noqa: PLC0415 — lazy

    lane = dict((throughput or {}).get("embed") or {})
    pretty = format_duration(lane.get("eta_seconds"))
    if not pretty or not lane.get("backlog"):
        return ""
    return f", and the whole embedding queue needs about {pretty} at the current rate"


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
            # Whether the wizard has ever run. Answered from the config, so it
            # is the SAME answer before and after the service wires itself —
            # see _is_configured for the restart bug that demanded it.
            "configured": _is_configured(uw),
            "started": False,
            "db_backend": configured_backend,
            "backend_in_use": "",
            # The stored choices, not an empty dict: a booting app still knows
            # what the user picked, and saying otherwise reopens the wizard.
            "slots": _slots_from_config(uw),
            "reembed": {},
            "counts": {},
            # Same shape as a live answer, so no client has to special-case a
            # booting backend into zeros of its own invention.
            "progress": build_progress({}),
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
    reembed = dict(data.get("reembed") or {})
    slots["storage"] = {
        "configured": backend.get("configured", configured_backend),
        "in_use": backend.get("in_use", ""),
        "ready": started,
        "reason": "" if started else "the store has not been opened yet",
        "vector": data.get("vector", {}),
    }
    return {
        "enabled": bool(data.get("enabled", enabled)),
        "configured": _is_configured(uw),
        "started": started,
        "db_backend": str(backend.get("configured") or configured_backend),
        "backend_in_use": str(backend.get("in_use") or ""),
        "slots": slots,
        # Empty unless an embedding-model switch is rebuilding the vector space
        # right now. The rebuilt items go to the FRONT of the embed queue, and
        # the vector leg reports itself unavailable until the swap — without
        # this a client would show a green, idle knowledge base.
        "reembed": reembed,
        "counts": data.get("counts", {}),
        "progress": data.get("progress") or build_progress(data.get("counts")),
        "pipeline": data.get("pipeline", {}),
        # Measured rate + ETA per lane. Empty until the worker has watched
        # itself long enough to answer honestly (throughput.py) — a client
        # renders "still measuring" rather than a first-minute extrapolation.
        "throughput": data.get("throughput", {}),
        "sources": data.get("sources", []),
        "jobs": data.get("jobs", []),
        "search_legs": _apply_reembed_to_legs(
            await _search_legs_async(cfg), reembed, data.get("throughput") or {}
        ),
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
                subscription_login_ready,
            )

            registry = BrainProviderRegistry()
            available_brains = set(registry.available())
            distill_chain = set(
                credential_ready_wiki_providers(
                    available=available_brains, config=cfg
                )
            )
            subscription_ready = {
                spec.id: subscription_login_ready(spec.id, registry=registry)
                for spec in provider_catalog.DISTILL_PROVIDERS
                if spec.auth_mode in provider_catalog.SUBSCRIPTION_AUTH_MODES
                and spec.id in available_brains
            }
        except Exception as exc:  # noqa: BLE001 — the catalog must never 500
            log.debug("distill chain probe failed: %s", exc, exc_info=True)
            distill_chain = set()
            subscription_ready = {}

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
                if spec.auth_mode in provider_catalog.SUBSCRIPTION_AUTH_MODES:
                    if subscription_ready.get(spec.id) is True:
                        return True, ""
                    return False, (
                        "subscription login is not connected — connect below, "
                        "or leave the slot on Automatic to use another ready "
                        "provider"
                    )
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

    # Activation can CHANGE the embedding model (the wizard asks for it, and
    # the Normal/Ultra switch re-sends the configured provider), so this route
    # owes the store the same registration PUT /settings performs. Without it
    # the config named one model while the store stayed pinned to another,
    # every vector was rejected, and the embed lane failed 100 % of its work
    # in silence — the 2026-07-28 forensic. `reconcile_space` is idempotent
    # and a no-op when the model already matches, so the ordinary "switch
    # Ultra back on" path costs two primary-key reads.
    space_rebuild = await _register_embedding_space(service, _uw_cfg(request), values)

    # The pipeline only starts while the mode is on, so it is started here —
    # after the flip, never before it.
    await service.ensure_started()

    response: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "embedding_space_rebuild": space_rebuild,
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
    #: Weight of the episodic-event leg (design doc 01, uw_events). 0 silences
    #: it without removing the stored events.
    rrf_event_weight: float | None = None
    recency_half_life_days: float | None = None
    confirm_reembed: bool = False


def _effective_embedding_model(uw: Any, changes: dict[str, str]) -> str:
    """The model name the embed stage will actually send after *changes*.

    The vector space is defined by the MODEL, not by who hosts it, and an empty
    model field means "this provider's default". Both matter for the re-embed
    question: resolving it here is what lets a pure provider switch that keeps
    the model skip the rebuild entirely.
    """
    model = changes.get("embedding_model")
    if model is None:
        model = str(getattr(uw, "embedding_model", "") or "")
    model = model.strip()
    if model:
        return model
    provider = changes.get("embedding_provider")
    if provider is None:
        provider = str(getattr(uw, "embedding_provider", "") or "")
    from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415

    return embeddings_mod.DEFAULT_MODELS.get(provider.strip(), "")


async def _register_embedding_space(
    service: Any, uw: Any, changes: dict[str, str]
) -> str:
    """Tell the store which vector space the config now names.

    Returns the store's verdict — ``"started"`` (a rebuild was registered),
    ``"rebuilding"``, ``"active"`` — or ``""`` when the store could not be
    asked. Never raises: the pipeline reconciles again on its next pass, so a
    failure here delays the switch, it does not lose it.
    """
    model = _effective_embedding_model(uw, changes)
    if not model:
        return ""
    try:
        store = await _store_of(service)
        return str(await store.reconcile_space(model) or "")
    except Exception:  # noqa: BLE001 — a settings save must not 500 on this
        log.warning(
            "UltraWiki: could not reconcile the embedding space; the pipeline "
            "reconciles on its next pass",
            exc_info=True,
        )
        return ""


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
        # Nothing to WRITE — but "the config already says this" is not the same
        # as "the store already does this", and treating them as equal is what
        # locked a maintainer out of his own repair path for a day (forensic
        # 2026-07-28). The config named gemini-embedding-001 while the store was
        # still pinned to text-embedding-3-large, so every vector was rejected;
        # re-picking Gemini produced exactly this branch — `changed: []`, a
        # cheerful 200, and nothing done. The screen that exists to fix the
        # divergence was the one screen that could not, and the harder he tried
        # the more certain it became.
        verdict = await _register_embedding_space(service, uw, {})
        return {
            "ok": True,
            "changed": [],
            "persisted": True,
            "reembed_started": verdict in ("started", "rebuilding"),
            "embedding_space_rebuild": verdict,
        }

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
    target_model = ""
    if embedding_change:
        from jarvis.ultrawiki.store import META_EMBED_MODEL  # noqa: PLC0415

        store = await _store_of(service)
        target_model = _effective_embedding_model(uw, changes)
        pinned_model = await store.get_meta(META_EMBED_MODEL)
        # Same model behind a different provider is the SAME vector space —
        # nothing to rebuild, so nothing to confirm either.
        if pinned_model and target_model and pinned_model != target_model:
            counts = await store.counts()
            vector_items = int(counts.embedded) + int(counts.distilled)
        if vector_items and not body.confirm_reembed:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "changing the embedding provider or model switches "
                        f"vector spaces, so the {vector_items} embedded items "
                        "have to be embedded again with the new model — "
                        "vectors of two models cannot be compared. Repeat the "
                        "request with confirm_reembed=true to start that "
                        "rebuild in the background. Semantic search keeps "
                        "answering from the current vectors the whole time; "
                        "the new space is swapped in only once it is complete."
                    ),
                    "vector_items": vector_items,
                },
            )

    applied: dict[str, Any] = {**changes, **ranking_changes}
    persisted, persist_error = _persist_slots(applied)
    _apply_live(request, applied)
    reembed_started = False
    if embedding_change and target_model:
        store = await _store_of(service)
        # Builds the new space ALONGSIDE the live one: the current vectors and
        # the ANN index stay untouched and keep serving search until the
        # pipeline has re-embedded everything (store.promote_pending_space).
        reembed_started = await store.begin_reembed(target_model)
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


class UpdateSourceBody(BaseModel):
    """Editable source fields; omitted values keep their current setting."""

    label: str | None = Field(default=None, min_length=1)
    config: dict[str, Any] | None = None
    areas: list[str] | None = None


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


@router.patch("/sources/{source_id}", summary="Update an UltraWiki source")
async def update_source(
    source_id: str, body: UpdateSourceBody, request: Request
) -> dict[str, Any]:
    """Change a source path, exclusions, label, or areas without re-approving."""
    service = _service(request)
    try:
        return await service.update_source(
            source_id,
            label=body.label,
            config=body.config,
            area_ids=body.areas,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("unknown source") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


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
# Export files — look before you import, and get the file here in the first place
# ---------------------------------------------------------------------------

#: Hard ceiling for one dropped export. Streamed in fixed chunks, so this
#: bounds DISK usage; memory stays at one chunk no matter how big the file is.
_MAX_EXPORT_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

_UPLOAD_CHUNK_BYTES = 1024 * 1024

#: Characters that turn a "filename" into a path, a Windows alternate data
#: stream, or a wildcard. A dropped file is stored under its OWN name, so the
#: name has to be a name — such an upload is refused, never silently renamed.
_UNSAFE_NAME_CHARS = frozenset('\\/:*?"<>|\x00')

_MAX_UPLOAD_NAME_CHARS = 200


def _safe_upload_name(raw: str) -> str:
    """The client's filename, or a 400 explaining why it was refused.

    Deliberately a REJECTION rather than a sanitisation: silently rewriting
    ``../../secrets.zip`` to ``secrets.zip`` would store the file somewhere
    the caller did not ask for and report success, which is exactly how a
    traversal attempt becomes an invisible one.
    """
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="the upload has no filename")
    if len(name) > _MAX_UPLOAD_NAME_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"the filename is longer than {_MAX_UPLOAD_NAME_CHARS} characters",
        )
    if (
        name in (".", "..")
        or set(name) & _UNSAFE_NAME_CHARS
        or any(ord(char) < 32 for char in name)
        or Path(name).name != name
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"refusing the filename {name!r}: an upload is stored under its "
                "own name, so the name may not contain a path, a drive letter, "
                "or a control character"
            ),
        )
    return name


def _discard_upload(directory: Path) -> None:
    """Remove a partial upload's folder; a failed cleanup is never fatal."""
    import shutil  # noqa: PLC0415 — lazy, only on the failure path

    shutil.rmtree(directory, ignore_errors=True)


class PreviewExportBody(BaseModel):
    """Which file or folder to inspect. Read-only — nothing is imported."""

    path: str = Field(min_length=1)


@router.post(
    "/export/preview", summary="Report what an export file or folder holds"
)
async def preview_export(
    body: PreviewExportBody, request: Request
) -> dict[str, Any]:
    """Detect the formats in a drop and count what is inside — before importing.

    "Approve and import everything" is a big button to press blind. This
    answers what "everything" IS first: how many mails, events, chats and
    rows were found, and which files will be skipped as unrecognised. It only
    reads — no source is registered, no byte is stored.

    The counts are exact when the pass could afford to be; a very large drop
    reports ``truncated`` and drops ``exact`` on the affected formats rather
    than presenting a partial number as the whole truth.
    """
    from jarvis.ultrawiki.connectors import export_import  # noqa: PLC0415 — lazy

    target = body.path.strip()
    if not target:
        raise HTTPException(status_code=400, detail="a path is required")
    # Walks the filesystem and reads bytes to count records — never on the
    # event loop, which also serves voice and chat.
    report = await asyncio.to_thread(export_import.scan_export, target)
    if not report.get("exists"):
        raise HTTPException(
            status_code=404,
            detail=f"nothing exists at {target!r} on this machine",
        )
    return report


@router.post(
    "/export/upload",
    summary="Upload an export file so it can be imported",
    openapi_extra={
        # It writes the uploaded bytes to this machine's disk.
        "x-jarvis-dangerous": True,
        # A multi-gigabyte Takeout archive over a slow link takes minutes; the
        # CLI's default client timeout would give up on a transfer that is
        # working perfectly.
        "x-jarvis-timeout-seconds": 600,
    },
)
async def upload_export(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency default
) -> dict[str, Any]:
    """Stream a dropped export file to disk and return the path to import from.

    The alternative — "type the full path to your Takeout archive" — is a
    non-starter on a machine the user is not sitting at, and unfriendly even
    on one they are. The bytes go to a fresh folder under the Jarvis data
    directory in fixed chunks, so a 2 GB archive costs one chunk of memory,
    and a transfer that exceeds the cap leaves nothing behind.
    """
    from jarvis.ultrawiki.connectors import export_import  # noqa: PLC0415 — lazy

    name = _safe_upload_name(file.filename or "")
    data_dir = getattr(getattr(_config(request), "memory", None), "data_dir", None)
    folder = export_import.uploads_dir(data_dir) / uuid.uuid4().hex
    target = folder / name

    try:
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"the upload folder could not be created ({type(exc).__name__}) "
                "— check that the Jarvis data directory is writable"
            ),
        ) from exc

    written = 0
    too_large = False
    try:
        handle = await asyncio.to_thread(target.open, "wb")
        try:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_EXPORT_UPLOAD_BYTES:
                    too_large = True
                    break
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
    except OSError as exc:
        await asyncio.to_thread(_discard_upload, folder)
        raise HTTPException(
            status_code=500,
            detail=f"the upload could not be written ({type(exc).__name__}: {exc})",
        ) from exc

    if too_large:
        await asyncio.to_thread(_discard_upload, folder)
        raise HTTPException(
            status_code=413,
            detail=(
                "the file is larger than "
                f"{_MAX_EXPORT_UPLOAD_BYTES // (1024**3)} GB — nothing was "
                "kept. Point the source at the file on this machine instead, "
                "which has no size limit at all."
            ),
        )
    if written == 0:
        await asyncio.to_thread(_discard_upload, folder)
        raise HTTPException(status_code=400, detail="the uploaded file is empty")

    return {
        "path": str(target),
        "name": name,
        "size": written,
        "detail": (
            "The file is on this machine. Preview it to see what it holds, "
            "then add it as a source."
        ),
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


@router.get("/items/{item_id}", summary="Read one stored item in full")
async def get_item(item_id: int, request: Request) -> dict[str, Any]:
    """Everything UltraWiki holds about one item — including the stored text.

    The inventory answers "which items are in there"; this answers "what does
    it actually KNOW about this one". Both halves matter: the raw text as
    captured (so a user can confirm nothing was mangled on the way in) AND the
    derived documents with their distillation and vector state (so "distilled"
    stops being a badge with nothing behind it).
    """
    service = _service(request)
    store = await _store_of(service)
    item = await store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown item {item_id}")

    documents: list[dict[str, Any]] = []
    try:
        rows = await store.item_documents(item_id)
    except Exception as exc:  # noqa: BLE001 — a missing derivation is not a 500
        log.debug("item documents unavailable for %s", item_id, exc_info=True)
        rows = []
        documents_error = f"{type(exc).__name__}"
    else:
        documents_error = ""
    for row in rows:
        distill: Any = None
        raw = row.get("distill_json")
        if raw:
            try:
                import json  # noqa: PLC0415 — lazy, only on the detail path

                distill = json.loads(raw)
            except (TypeError, ValueError):
                # Keep the unparsed text rather than dropping it: a broken
                # distillation is still evidence of what happened.
                distill = {"raw": str(raw)[:2000]}
        documents.append(
            {
                "id": row.get("id"),
                "doc_type": row.get("doc_type"),
                "text": str(row.get("text_norm") or ""),
                "distill": distill,
                "has_vector": bool(row.get("has_vector")),
                "created_at": row.get("created_at"),
            }
        )

    return {
        "id": item.get("id"),
        "source_id": item.get("source_id"),
        "external_id": item.get("external_id"),
        "title": item.get("title") or "",
        # The text EXACTLY as it was captured — the point of the whole view.
        "body": item.get("body_raw") or "",
        "permalink": item.get("permalink") or "",
        "timestamp_utc": item.get("timestamp_utc") or "",
        "author": item.get("author_raw") or "",
        "thread_key": item.get("thread_key") or "",
        "state": item.get("state") or "",
        "areas": item.get("areas") or [],
        "content_hash": item.get("content_hash") or "",
        "attempt_count": item.get("attempt_count") or 0,
        "last_error": item.get("last_error") or "",
        "deleted_at": item.get("deleted_at"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "documents": documents,
        "documents_error": documents_error,
    }


@router.get("/reconcile", summary="Did everything that was read actually land?")
async def reconcile_sources(request: Request) -> dict[str, Any]:
    """Per-source proof that the last import is fully in the database.

    "It says 4 689 items — but is that everything?" is a fair question that no
    count alone can answer, because a count cannot distinguish "all of it" from
    "as much as we managed". The last finished run reports exactly how many
    items it READ (new + changed + unchanged); the store reports how many it
    HOLDS for that source. When the stored number covers the read number,
    everything the connector handed over is in the database — and when it does
    not, the difference is the honest answer instead of a reassuring total.
    """
    service = _service(request)
    status = await service.status()
    rows: list[dict[str, Any]] = []
    for source in status.get("sources", []):
        counts = dict(source.get("counts") or {})
        stored = int(counts.get("total") or 0)
        outcome = dict(source.get("last_outcome") or {})
        finished = bool(outcome)
        read = (
            int(outcome.get("new") or 0)
            + int(outcome.get("changed") or 0)
            + int(outcome.get("unchanged") or 0)
        )
        if not finished:
            verdict, detail = "never_imported", "This source has never been read."
        elif outcome.get("status") != "done":
            verdict = "incomplete"
            detail = (
                f"The last import ended as '{outcome.get('status')}', so it may "
                "not have read everything."
            )
        elif stored >= read:
            verdict = "complete"
            detail = (
                f"All {read} item(s) the last import read are in the database."
            )
        else:
            verdict = "short"
            detail = (
                f"The last import read {read} item(s) but only {stored} are "
                "stored — {0} are missing.".replace("{0}", str(read - stored))
            )
        rows.append(
            {
                "source_id": source.get("id"),
                "label": source.get("label") or source.get("id"),
                "verdict": verdict,
                "detail": detail,
                "stored": stored,
                "read": read,
                "new": int(outcome.get("new") or 0),
                "changed": int(outcome.get("changed") or 0),
                "unchanged": int(outcome.get("unchanged") or 0),
                "tombstoned": int(outcome.get("tombstoned") or 0),
                "finished_at": outcome.get("finished_at"),
                "last_error": source.get("last_error") or "",
            }
        )
    complete = [r for r in rows if r["verdict"] == "complete"]
    return {
        "sources": rows,
        "total_stored": sum(r["stored"] for r in rows),
        "all_complete": bool(rows) and len(complete) == len(rows),
        "complete": len(complete),
        "total_sources": len(rows),
    }


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
# The platform export guide
# ---------------------------------------------------------------------------


@router.get(
    "/platforms",
    summary="Where each platform keeps its own export, and what comes out",
)
async def list_platform_exports(
    category: str = Query("", description="Limit to one category"),
    q: str = Query("", description="Free-text search over names and descriptions"),
) -> dict[str, Any]:
    """How to get your data OUT of services we ship no connector for.

    Every route named here is the platform's own export feature, available
    worldwide — this is deliberately not built on any one jurisdiction's right
    to data portability (charter §3). Nothing is fetched or executed: the
    answer is a catalog the caller reads, and the export it leads to is then
    dropped on the export-file source like any other archive.
    """
    from jarvis.ultrawiki import platform_guide  # noqa: PLC0415 — lazy (AP-26)

    if q.strip():
        entries = platform_guide.search_platforms(q)
    else:
        entries = platform_guide.list_platforms(category)
    return {
        "platforms": [platform_guide.as_dict(entry) for entry in entries],
        "total": len(entries),
        "categories": list(platform_guide.CATEGORIES),
    }


@router.get(
    "/platforms/{platform_id}",
    summary="Read one platform's export instructions",
)
async def get_platform_export(platform_id: str) -> dict[str, Any]:
    """One catalog entry in full."""
    from jarvis.ultrawiki import platform_guide  # noqa: PLC0415 — lazy (AP-26)

    entry = platform_guide.get_platform(platform_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No export guide for {platform_id!r}. "
                "GET /api/ultrawiki/platforms lists every platform covered."
            ),
        )
    return platform_guide.as_dict(entry)


# ---------------------------------------------------------------------------
# Episodic events (design doc 01 · uw_events)
# ---------------------------------------------------------------------------


def _event_kind_or_400(raw: str) -> str | None:
    """Validate an event-kind filter against the canonical enum, or 400."""
    wanted = (raw or "").strip()
    if not wanted:
        return None
    from jarvis.ultrawiki.events import EventKind  # noqa: PLC0415 — lazy (AP-26)

    known = {member.value for member in EventKind}
    if wanted not in known:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown event kind {wanted!r} (one of: "
                f"{', '.join(sorted(known))})"
            ),
        )
    return wanted


@router.get("/events", summary="List episodic events with their absolute dates")
async def list_events(
    request: Request,
    since: str = Query(
        default="",
        description="ISO-8601 lower bound on when the event HAPPENED (valid time)",
    ),
    until: str = Query(
        default="", description="ISO-8601 upper bound on when the event happened"
    ),
    kind: str = Query(
        default="",
        description="Only events of this kind (meal, travel, meeting, purchase, milestone, other)",
    ),
    entity_id: int | None = Query(
        default=None, description="Only events this person/place took part in"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """What happened, when, where and with whom — newest first.

    The window bounds VALID time (when it happened), never the recorded time,
    and matches by overlap: an event the source only pinned down to a month is
    returned for a question about a day inside it. Every row carries an
    absolute ``occurred_at`` plus ``time_anchor``, which says whether the
    source stated that date, whether it was resolved from a relative
    expression, or whether it is merely the moment the item was recorded.
    """
    service = _service(request)
    store = await _store_of(service)
    rows = await store.list_events(
        since=since.strip() or None,
        until=until.strip() or None,
        kind=_event_kind_or_400(kind),
        entity_id=entity_id,
        limit=limit,
        offset=offset,
    )
    return {"events": rows, "total": len(rows), "limit": limit, "offset": offset}


@router.get("/events/{event_id}", summary="Read one episodic event in full")
async def get_event(event_id: int, request: Request) -> dict[str, Any]:
    """One event with its participants, place and evidence permalink."""
    service = _service(request)
    store = await _store_of(service)
    event = await store.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"unknown event {event_id}")
    return event


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class AskBody(BaseModel):
    """One evidence-grounded Ask request."""

    question: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=10, ge=1, le=20)
    area: str | None = Field(default=None, max_length=200)


ANSWER_STATUS_ANSWERED = "answered"
ANSWER_STATUS_INSUFFICIENT = "insufficient_evidence"
ANSWER_STATUS_NO_EVIDENCE = "no_evidence"
ANSWER_STATUS_UNAVAILABLE = "answer_unavailable"
ULTRAWIKI_ANSWER_STATUSES = (
    ANSWER_STATUS_ANSWERED,
    ANSWER_STATUS_INSUFFICIENT,
    ANSWER_STATUS_NO_EVIDENCE,
    ANSWER_STATUS_UNAVAILABLE,
)


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


@router.get(
    "/word-search",
    summary="Find a word's nearest meaning-neighbours and the passages they reach",
)
async def word_search_ultrawiki(
    request: Request,
    word: str = Query(..., min_length=1, description="A single word or short phrase"),
    k: int = Query(default=10, ge=1, le=50, description="How many items to return"),
    neighbours: int = Query(
        default=0,
        ge=0,
        le=50,
        description="Meaning-neighbours to expand with; 0 uses the configured default",
    ),
    area: str | None = Query(default=None, description="Optional area id filter"),
) -> dict[str, Any]:
    """Expand one word into the ~20 terms nearest it by meaning, then retrieve.

    Unlike ``/search`` this answers a WORD, not a question: the neighbourhood
    drives retrieval against the chunked documents, so hits point at the
    passage that carries the vocabulary rather than at the whole item.
    ``status`` names why the result looks the way it does — an empty list is
    never left for the caller to interpret.
    """
    service = _require_active(request)
    configured = _uw_cfg(request)
    wanted = int(neighbours) or int(
        getattr(configured, "word_search_neighbours", 20) or 20
    )
    outcome = await service.word_search(
        word, k=k, neighbours=wanted, area_id=area
    )
    return {
        "word": outcome.word,
        "status": outcome.status,
        "neighbour_source": outcome.neighbour_source,
        "reason": outcome.reason,
        "neighbours": [
            dataclasses.asdict(neighbour) for neighbour in outcome.neighbours
        ],
        "results": [dataclasses.asdict(hit) for hit in outcome.hits],
        "total": len(outcome.hits),
        "lexicon": dict(outcome.lexicon),
    }


@router.post(
    "/lexicon/rebuild",
    summary="Throw the word vocabulary away and let it be recounted",
    # Destructive to derived data and NOT free: refilling the lexicon costs
    # one embedding call per batch of terms. The CLI must ask before running
    # it, so the flag is declared even though the path matches no marker.
    openapi_extra={"x-jarvis-dangerous": True},
)
async def rebuild_ultrawiki_lexicon(request: Request) -> dict[str, Any]:
    """Reset the word lexicon so the background pass harvests it from scratch.

    The repair for a vocabulary whose frequencies have drifted after many
    deletions, and the way to rebuild term vectors after an embedding-model
    switch. Destructive only to derived data: nothing imported is touched, and
    word search keeps answering through the co-occurrence path while the
    lexicon refills.
    """
    service = _require_active(request)
    return await service.rebuild_lexicon()


@router.post(
    "/ask",
    summary="Answer a question from UltraWiki evidence with citations",
)
async def ask_ultrawiki(body: AskBody, request: Request) -> dict[str, Any]:
    """Retrieve evidence, then synthesize through the cross-family chat chain.

    Search remains useful when every chat provider is unavailable: this route
    returns the evidence plus an honest ``answer_unavailable`` state instead
    of turning a synthesis outage into a failed search.
    """
    service = _require_active(request)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")
    hits = await service.search(
        question,
        k=body.k,
        area_id=body.area,
    )
    rows = [
        dataclasses.asdict(hit) if dataclasses.is_dataclass(hit) else dict(hit)
        for hit in hits
    ]
    response: dict[str, Any] = {
        "query": question,
        "question": question,
        "answer": "",
        "answer_status": (
            ANSWER_STATUS_NO_EVIDENCE if not rows else ANSWER_STATUS_UNAVAILABLE
        ),
        "provider": "",
        "citations": [],
        "results": rows,
        "total": len(rows),
    }
    if not rows:
        return response

    from jarvis.ultrawiki.answer import (  # noqa: PLC0415 — lazy (AP-26)
        AnswerUnavailable,
        answer_question,
    )

    try:
        synthesis = await answer_question(_config(request), question, hits)
    except AnswerUnavailable as exc:
        log.info("UltraWiki Ask synthesis unavailable: %s", exc)
        response["synthesis_error"] = str(exc)
        return response
    response.update(
        {
            "answer": synthesis.answer,
            "answer_status": synthesis.status,
            "provider": synthesis.provider,
            "citations": list(synthesis.citations),
        }
    )
    return response
