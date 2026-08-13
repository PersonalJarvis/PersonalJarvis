"""REST API for assistant modes — the shelf of characters the user picks from.

Every action is an endpoint (the CLI-first contract): the modes screen, the
voice interviewer and the ``jarvis modes`` commands all drive these same
routes, so what the UI can do and what a spoken command can do never drift
apart.

Endpoints (prefix ``/api/modes``):

* ``GET    /``                  → every mode + which one is active
* ``GET    /{slug}``            → one mode in full
* ``PUT    /active``            → switch the active mode (applies next turn)
* ``POST   /``                  → create or replace a mode
* ``DELETE /{slug}``            → delete a user mode (or a user copy of a built-in)
* ``POST   /{slug}/restore``    → drop the user copy, bring the built-in back

Anti-drift (``docs/anti-drift-three-layer.md``): the ``Verbosity`` and
``Proactivity`` Literals below are asserted against ``modes.VERBOSITIES`` /
``modes.PROACTIVITIES`` at import time, so adding a value in one place and
forgetting the other fails at startup rather than on the one request that
happens to carry the new string.
"""

from __future__ import annotations

import logging
from typing import Literal, get_args

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.brain import modes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/modes", tags=["modes"])

Verbosity = Literal["brief", "normal", "rich"]
Proactivity = Literal["reactive", "normal", "forward"]

# D2 of the anti-drift doc: an import-time assertion, not a test somebody has to
# remember to run.
assert set(get_args(Verbosity)) == set(modes.VERBOSITIES), (
    "Verbosity Literal drifted from modes.VERBOSITIES"
)
assert set(get_args(Proactivity)) == set(modes.PROACTIVITIES), (
    "Proactivity Literal drifted from modes.PROACTIVITIES"
)


class ActiveModeBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)


class ModeBody(BaseModel):
    """One mode as the UI and the voice interviewer submit it."""

    # Optional: a mode created by voice has a NAME the user said out loud and no
    # slug at all, so the server derives one rather than making the caller
    # guess the grammar.
    slug: str = Field("", max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    character: str = Field(..., min_length=1, max_length=20_000)
    emoji: str = Field("", max_length=16)
    description: str = Field("", max_length=280)
    voice: str = Field("", max_length=80)
    verbosity: Verbosity = "normal"
    proactivity: Proactivity = "normal"


def _payload() -> dict[str, object]:
    active = modes.active_slug()
    return {
        "modes": [m.to_payload() for m in modes.list_modes()],
        "active": active,
        # What the section override is doing, if anything. The UI needs to be
        # able to say "coding mode is on because you are in the Agentic IDE"
        # rather than showing a switch the user did not touch and cannot
        # explain — an unexplained mode is exactly what this feature replaced.
        "section_override": modes.section_override() or "",
        "verbosities": list(modes.VERBOSITIES),
        "proactivities": list(modes.PROACTIVITIES),
    }


@router.get("", summary="Every mode and which one is active")
async def list_modes() -> dict[str, object]:
    """The full shelf, built-ins first, plus the active slug."""
    return _payload()


@router.get("/{slug}", summary="One mode in full")
async def get_mode(slug: str) -> dict[str, object]:
    mode = modes.get_mode(slug)
    if mode is None:
        raise HTTPException(status_code=404, detail=f"No mode called {slug!r}.")
    return mode.to_payload()


@router.put("/active", summary="Switch the active mode")
async def set_active(body: ActiveModeBody) -> dict[str, object]:
    """Switch modes. Applies on the next turn — voice and chat alike, no restart.

    A section override (the Agentic IDE) still wins while it is in force, so the
    response reports the slug ACTUALLY in effect, not the one that was asked
    for. Reporting the request back would be a small lie the UI would then show.
    """
    try:
        mode = modes.set_active(body.slug)
    except modes.ModeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "chosen": mode.slug, "restart_required": False, **_payload()}


@router.post("", summary="Create or replace a mode")
async def save_mode(body: ModeBody) -> dict[str, object]:
    """Write a mode. Creating one does not switch to it — that is a separate act."""
    try:
        slug = modes.normalize_slug(body.slug or body.name)
    except modes.ModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        mode = modes.save_mode(
            slug=slug,
            name=body.name,
            character=body.character,
            emoji=body.emoji,
            description=body.description,
            voice=body.voice,
            verbosity=body.verbosity,
            proactivity=body.proactivity,
        )
    except modes.ModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc

    return {"ok": True, "mode": mode.to_payload(), **_payload()}


@router.delete("/{slug}", summary="Delete a user mode")
async def delete_mode(slug: str) -> dict[str, object]:
    """Remove a user mode. Built-ins are refused; a user COPY of one is allowed."""
    try:
        removed = modes.delete_mode(slug)
    except modes.ModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "removed": removed, **_payload()}


@router.post("/{slug}/restore", summary="Restore a built-in mode")
async def restore_builtin(slug: str) -> dict[str, object]:
    """Throw away the user's edits to a built-in and bring the packaged one back."""
    try:
        restored = modes.restore_builtin(slug)
    except modes.ModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "restored": restored, **_payload()}
