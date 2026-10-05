"""Imported society figures — a person's own characters, kept in the data dir.

The character pipeline (docs/agent-society/character-pipeline.md §8, the import
lane) lets anyone bring a figure of their own: a GLB that honours the same
contract every shipped figure honours. It is checked by the SAME gate the CI
runs (``scripts/ci/check_society_figures.py``), so a figure that faces the wrong
way or drifts its root is refused with the gate's own reasons instead of walking
backwards on the island. Accepted files live under ``DATA_DIR/society/figures``
and are served from here; a recipe references one by its URL (``model``).

There are two deliberately separate lanes. Imported GLBs stay private on the
person's own machine. Shared-catalog entries are recipe-only JSON referencing
reviewed built-in assets; they never upload a model or contact a remote service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.core.config import DATA_DIR
from jarvis.core.path_safety import UnsafePathError, safe_child
from jarvis.society.figure_sharing import PUBLIC_FIGURE_LICENSES, SharedFigureDraft

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/society/figures", tags=["society"])

#: A base figure with nine 30-fps clips weighs ~400 KB; 12 MB leaves room for a
#: hand-built figure with a bigger sheet without inviting a stray video.
MAX_BYTES = 12 * 1024 * 1024
_GLB_MAGIC = b"glTF"
_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_society_figures.py"
_PUBLIC_CATALOG = (
    _REPO
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "components"
    / "society"
    / "figures"
    / "catalog.json"
)
_SAFE_NAME = re.compile(r"[^a-z0-9]+")
_SHARE_ID = re.compile(r"^[0-9a-f]{16}$")
_SHARE_LOCK = threading.Lock()
_SHARE_REPORT_CAP = 100


class SharedFigureReport(BaseModel):
    """One local moderation report for a shared figure catalog entry."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_is_meaningful(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("report reason must not be blank")
        return clean


def figures_dir() -> Path:
    return DATA_DIR / "society" / "figures"


def _share_catalog_path() -> Path:
    return figures_dir().parent / "shared-figures.json"


def _empty_share_catalog() -> dict[str, Any]:
    return {"version": 1, "figures": {}}


def _read_share_catalog() -> dict[str, Any]:
    path = _share_catalog_path()
    if not path.exists():
        return _empty_share_catalog()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(doc, dict)
        or doc.get("version") != 1
        or not isinstance(doc.get("figures"), dict)
    ):
        raise ValueError("invalid shared figure catalog")
    return doc


def _write_share_catalog(doc: dict[str, Any]) -> None:
    path = _share_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _validate_shared_assets(body: SharedFigureDraft) -> None:
    """Require every shared recipe asset to exist in the shipped public catalog."""
    try:
        catalog = json.loads(_PUBLIC_CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("shared figure catalog asset index unavailable", exc_info=True)
        raise HTTPException(503, "shared figure asset catalog unavailable") from exc

    recipe = body.normalized()["recipe"]
    archetype = str(recipe.get("archetype") or "")
    base_name = str(recipe.get("base") or "")
    bases = catalog.get("bases") if isinstance(catalog, dict) else None
    parts = catalog.get("parts") if isinstance(catalog, dict) else None
    if not isinstance(bases, list) or not isinstance(parts, list):
        raise HTTPException(503, "shared figure asset catalog unavailable")

    base = next(
        (
            row
            for row in bases
            if isinstance(row, dict)
            and row.get("archetype") == archetype
            and row.get("base") == base_name
            and row.get("license") in PUBLIC_FIGURE_LICENSES
            and (archetype, base_name) != ("spirit", "gigi")
        ),
        None,
    )
    if base is None:
        raise HTTPException(422, "shared figure base is not in the reviewed public catalog")

    recipe_parts = recipe.get("parts") or {}
    if not isinstance(recipe_parts, dict):
        raise HTTPException(422, "shared figure parts must be a slot map")
    for slot, part_id in recipe_parts.items():
        match = next(
            (
                row
                for row in parts
                if isinstance(row, dict)
                and row.get("id") == part_id
                and row.get("slot") == slot
                and row.get("archetype") == archetype
                and row.get("license") in PUBLIC_FIGURE_LICENSES
            ),
            None,
        )
        if match is None:
            raise HTTPException(
                422,
                f"shared figure part {part_id!r} is not a reviewed {archetype} {slot!r} asset",
            )


def _shared_figure_id(normalized: dict[str, Any]) -> str:
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _public_share_row(row: dict[str, Any]) -> dict[str, Any]:
    """Public catalog projection; moderation reasons stay local."""
    return {
        key: value
        for key, value in row.items()
        if key != "reports"
    } | {"report_count": len(row.get("reports") or [])}


def _figure_path(file_name: str) -> Path | None:
    """The stored figure called *file_name*, or None when the name is not one.

    Only a plain ``*.glb`` component inside :func:`figures_dir` qualifies, so a
    request can never read or delete a file elsewhere (``..``, separators, or a
    Windows drive-relative name such as ``C:x.glb``).
    """
    if not file_name.endswith(".glb"):
        return None
    try:
        return safe_child(figures_dir(), file_name)
    except UnsafePathError:  # an escaping name is simply not a figure; the route answers 404
        return None


def _load_gate():
    if "check_society_figures" in sys.modules:
        return sys.modules["check_society_figures"]
    if not _GATE.exists():
        return None
    spec = importlib.util.spec_from_file_location("check_society_figures", _GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["check_society_figures"] = module
    spec.loader.exec_module(module)
    return module


def _slug(name: str) -> str:
    return _SAFE_NAME.sub("-", name.lower()).strip("-")[:40] or "figure"


def _extras(path: Path) -> dict[str, Any]:
    gate = _load_gate()
    if gate is None:
        return {}
    tools = gate._load_tools()
    try:
        doc = tools.read_glb(path).doc
    except (ValueError, OSError):
        return {}
    return (doc.get("asset", {}).get("extras") or {}).get("jarvis_figure") or {}


def _describe(path: Path) -> dict[str, Any]:
    extras = _extras(path)
    return {
        "id": path.stem,
        "file": path.name,
        "url": f"/api/society/figures/{path.name}",
        "bytes": path.stat().st_size,
        "archetype": extras.get("archetype"),
        "height_m": extras.get("height_m"),
        "clips": sorted((extras.get("clips") or {}).keys()),
        "source": extras.get("source", ""),
    }


@router.get("")
async def list_figures() -> dict[str, Any]:
    folder = figures_dir()
    if not folder.exists():
        return {"figures": [], "total": 0}
    rows = await run_in_threadpool(lambda: [_describe(p) for p in sorted(folder.glob("*.glb"))])
    return {"figures": rows, "total": len(rows)}


@router.post("")
async def import_figure(request: Request, name: str = Query("figure")) -> dict[str, Any]:
    """Accept one GLB (raw body), run the figure gate, keep it only when it passes."""
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(413, f"figure larger than {MAX_BYTES // (1024 * 1024)} MB")
    if len(body) < 12 or body[:4] != _GLB_MAGIC:
        raise HTTPException(400, "not a binary glTF (.glb) file")
    digest = hashlib.sha256(body).hexdigest()[:10]
    folder = figures_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_slug(name)}-{digest}.glb"

    def _check_and_store() -> list[str]:
        gate = _load_gate()
        if gate is None:
            return ["the figure gate is not available on this install"]
        probe = path.with_suffix(".pending.glb")
        probe.write_bytes(body)
        try:
            problems = gate.check_file(probe, require_ledger=False)
            if problems:
                return problems
            probe.replace(path)
            return []
        finally:
            if probe.exists():
                probe.unlink()

    problems = await run_in_threadpool(_check_and_store)
    if problems:
        # The gate's own sentences: what to fix, in the order it found them.
        return {"accepted": False, "problems": problems}
    log.info("society figure imported: %s (%d bytes)", path.name, len(body))
    return {"accepted": True, "figure": _describe(path)}


@router.post("/share/validate", openapi_extra={"x-jarvis-readonly": True})
def validate_shared_figure(body: SharedFigureDraft) -> dict[str, Any]:
    """Validate a recipe-only marketplace candidate without publishing or writing files."""
    _validate_shared_assets(body)
    return {"shareable": True, "draft": body.normalized()}


@router.get("/share", openapi_extra={"x-jarvis-readonly": True})
async def list_shared_figures() -> dict[str, Any]:
    """List locally published, non-delisted recipe-only marketplace entries."""

    def _list() -> list[dict[str, Any]]:
        with _SHARE_LOCK:
            doc = _read_share_catalog()
            rows = [
                _public_share_row(row)
                for row in doc["figures"].values()
                if isinstance(row, dict) and row.get("status") == "active"
            ]
            return sorted(
                rows,
                key=lambda row: (int(row.get("created_ms") or 0), str(row.get("id"))),
            )

    try:
        rows = await run_in_threadpool(_list)
    except (OSError, ValueError) as exc:
        log.warning("shared figure catalog read failed", exc_info=True)
        raise HTTPException(500, "shared figure catalog unavailable") from exc
    return {"figures": rows, "total": len(rows)}


@router.post("/share", openapi_extra={"x-jarvis-dangerous": True})
async def publish_shared_figure(body: SharedFigureDraft) -> dict[str, Any]:
    """Publish one reviewed recipe into the local shared catalog."""
    _validate_shared_assets(body)
    normalized = body.normalized()
    figure_id = _shared_figure_id(normalized)

    def _publish() -> tuple[dict[str, Any], bool]:
        with _SHARE_LOCK:
            doc = _read_share_catalog()
            existing = doc["figures"].get(figure_id)
            if isinstance(existing, dict):
                if existing.get("status") == "delisted":
                    raise PermissionError("delisted shared figures cannot be republished implicitly")
                return existing, False
            now = int(time.time() * 1000)
            row = {
                "id": figure_id,
                **normalized,
                "status": "active",
                "reports": [],
                "created_ms": now,
                "updated_ms": now,
            }
            doc["figures"][figure_id] = row
            _write_share_catalog(doc)
            return row, True

    try:
        row, created = await run_in_threadpool(_publish)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        log.warning("shared figure catalog write failed", exc_info=True)
        raise HTTPException(500, "shared figure catalog unavailable") from exc
    return {"published": created, "figure": _public_share_row(row)}


@router.post("/share/{share_id}/report")
async def report_shared_figure(share_id: str, body: SharedFigureReport) -> dict[str, Any]:
    """Persist a local moderation report without auto-delisting the entry."""
    if not _SHARE_ID.fullmatch(share_id):
        raise HTTPException(404, "no such shared figure")

    def _report() -> dict[str, Any]:
        with _SHARE_LOCK:
            doc = _read_share_catalog()
            row = doc["figures"].get(share_id)
            if not isinstance(row, dict) or row.get("status") != "active":
                raise KeyError(share_id)
            reports = list(row.get("reports") or [])
            reports.append({"reason": body.reason, "reported_ms": int(time.time() * 1000)})
            row["reports"] = reports[-_SHARE_REPORT_CAP:]
            row["updated_ms"] = int(time.time() * 1000)
            _write_share_catalog(doc)
            return row

    try:
        row = await run_in_threadpool(_report)
    except KeyError as exc:
        raise HTTPException(404, "no such shared figure") from exc
    except (OSError, ValueError) as exc:
        log.warning("shared figure report failed", exc_info=True)
        raise HTTPException(500, "shared figure catalog unavailable") from exc
    return {"reported": share_id, "reports": len(row.get("reports") or [])}


@router.delete("/share/{share_id}", openapi_extra={"x-jarvis-dangerous": True})
async def delist_shared_figure(share_id: str) -> dict[str, Any]:
    """Delist one shared recipe while preserving its moderation history."""
    if not _SHARE_ID.fullmatch(share_id):
        raise HTTPException(404, "no such shared figure")

    def _delist() -> None:
        with _SHARE_LOCK:
            doc = _read_share_catalog()
            row = doc["figures"].get(share_id)
            if not isinstance(row, dict):
                raise KeyError(share_id)
            row["status"] = "delisted"
            row["updated_ms"] = int(time.time() * 1000)
            _write_share_catalog(doc)

    try:
        await run_in_threadpool(_delist)
    except KeyError as exc:
        raise HTTPException(404, "no such shared figure") from exc
    except (OSError, ValueError) as exc:
        log.warning("shared figure delist failed", exc_info=True)
        raise HTTPException(500, "shared figure catalog unavailable") from exc
    return {"delisted": share_id}


@router.get("/{file_name}")
def get_figure(file_name: str) -> FileResponse:
    path = _figure_path(file_name)
    if path is None or not path.is_file():
        raise HTTPException(404, "no such figure")
    return FileResponse(
        path, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=31536000"}
    )


@router.delete("/{file_name}", openapi_extra={"x-jarvis-dangerous": True})
async def delete_figure(file_name: str) -> dict[str, Any]:
    path = _figure_path(file_name)
    if path is None or not path.is_file():
        raise HTTPException(404, "no such figure")
    await run_in_threadpool(path.unlink)
    return {"deleted": file_name}
