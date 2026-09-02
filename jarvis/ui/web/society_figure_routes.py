"""Imported society figures — a person's own characters, kept in the data dir.

The character pipeline (docs/agent-society/character-pipeline.md §8, the import
lane) lets anyone bring a figure of their own: a GLB that honours the same
contract every shipped figure honours. It is checked by the SAME gate the CI
runs (``scripts/ci/check_society_figures.py``), so a figure that faces the wrong
way or drifts its root is refused with the gate's own reasons instead of walking
backwards on the island. Accepted files live under ``DATA_DIR/society/figures``
and are served from here; a recipe references one by its URL (``model``).

Not a marketplace lane: nothing here is shared, uploaded elsewhere, or
attributed. It is the person's own file on the person's own machine.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from jarvis.core.config import DATA_DIR

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/society/figures", tags=["society"])

#: A base figure with nine 30-fps clips weighs ~400 KB; 12 MB leaves room for a
#: hand-built figure with a bigger sheet without inviting a stray video.
MAX_BYTES = 12 * 1024 * 1024
_GLB_MAGIC = b"glTF"
_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_society_figures.py"
_SAFE_NAME = re.compile(r"[^a-z0-9]+")


def figures_dir() -> Path:
    return DATA_DIR / "society" / "figures"


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


@router.get("/{file_name}")
def get_figure(file_name: str) -> FileResponse:
    path = figures_dir() / file_name
    if (
        not file_name.endswith(".glb")
        or "/" in file_name
        or "\\" in file_name
        or not path.is_file()
    ):
        raise HTTPException(404, "no such figure")
    return FileResponse(
        path, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=31536000"}
    )


@router.delete("/{file_name}", openapi_extra={"x-jarvis-dangerous": True})
async def delete_figure(file_name: str) -> dict[str, Any]:
    path = figures_dir() / file_name
    if (
        not file_name.endswith(".glb")
        or "/" in file_name
        or "\\" in file_name
        or not path.is_file()
    ):
        raise HTTPException(404, "no such figure")
    await run_in_threadpool(path.unlink)
    return {"deleted": file_name}

