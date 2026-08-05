"""REST API for the Socials section — the project's social-media links.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/socials          → {"entries": [...]} sorted by ``order``.
    POST   /api/socials          → create one (server assigns id + order); 201.
    PATCH  /api/socials/{id}      → edit platform/label/url/enabled; returns it.
    DELETE /api/socials/{id}      → remove (idempotent → 200).

Storage is a dedicated JSON file under ``user_data_dir()/data/socials.json``,
written atomically (tempfile + ``os.replace``, mirroring the avatar write in
``profile_routes.py`` and ``self_mod/writer.py``).

Why a file and not ``jarvis.toml``: the drift-guard daemon (BUG-010) watches
``jarvis.toml`` and would fire on every social edit; social links are *content*,
not *config*. A JSON file satisfies the cloud-first €5-VPS doctrine just as well
(no Windows / GPU / native dependency) and the store does NOT depend on the
Brain — so it works headless / with MockBrain (like the avatar endpoints).

On first run (no file yet) the store is seeded with the project's Discord invite
and the two GitHub links; deleting every entry leaves an empty file that is
NOT re-seeded, so the user stays in control of the list.

Security: a stored ``url`` becomes an ``href`` in the UI, so the scheme is
restricted to ``http``/``https`` — a ``javascript:`` URL is rejected (XSS guard).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jarvis.core.branding import OFFICIAL_REPO_URL

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/socials", tags=["socials"])

# Serializes the read-modify-write of socials.json. Endpoints are sync ``def``
# (run in FastAPI's threadpool), so a threading.Lock is the right primitive.
_LOCK = threading.Lock()

_MAX_URL_LEN = 2048
_MAX_LABEL_LEN = 200
_MAX_PLATFORM_LEN = 64
_ALLOWED_SCHEMES = frozenset({"http", "https"})


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------


def _data_file() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "data" / "socials.json"


def _seed_entries() -> list[dict[str, Any]]:
    """The first-run seed: Discord on top, then the two GitHub links.

    The GitHub profile URL is a placeholder the maintainer corrects to their
    real handle via the UI — the org page is a sane stand-in until then.
    """
    raw = [
        ("discord", "Discord", "https://discord.gg/x7USduHxbc"),
        ("github", "GitHub (Repo)", OFFICIAL_REPO_URL),
        ("github", "GitHub (Profile)", "https://github.com/PersonalJarvis"),
        # X: the project's public X presence. Maintainer directive 2026-07-18:
        # the former @PersonalJarvis account is defunct; the project posts from
        # the maintainer's public handle @Ruben_Luetke, so every X link in the
        # repo points there. Users edit or remove the entry via the UI.
        ("x", "Personal Jarvis", "https://x.com/Ruben_Luetke"),
        ("instagram", "Instagram", "https://www.instagram.com/personaljarvis/"),
    ]
    return [
        {
            "id": uuid.uuid4().hex,
            "platform": platform,
            "label": label,
            "url": url,
            "enabled": True,
            "order": i,
        }
        for i, (platform, label, url) in enumerate(raw)
    ]


def _read_entries() -> list[dict[str, Any]]:
    """Load entries. Seeds + writes the file on first run (file missing).

    A file that exists but is empty or unparseable yields ``[]`` and is NOT
    re-seeded — once the user has a file, the list is theirs.
    """
    path = _data_file()
    if not path.exists():
        seeds = _seed_entries()
        _write_entries(seeds)
        return seeds
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — corrupt file degrades to empty
        log.warning("socials: could not parse %s — %s", path, exc)
        return []
    entries = raw.get("entries") if isinstance(raw, dict) else None
    return entries if isinstance(entries, list) else []


def _write_entries(entries: list[dict[str, Any]]) -> None:
    """Atomic write: tempfile in the parent dir → ``os.replace``."""
    path = _data_file()
    payload = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2)
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".socials.", suffix=".json", dir=str(dir_))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def _clean_url(url: str) -> str:
    """Return a trimmed http(s) URL or raise 400.

    The value ends up in an ``href`` — restricting the scheme blocks
    ``javascript:``/``data:`` injection. Relative URLs (no scheme) are rejected
    too: a social link must be an absolute external URL.
    """
    value = (url or "").strip()
    if not value or len(value) > _MAX_URL_LEN:
        raise HTTPException(status_code=400, detail="URL is required and must be < 2048 chars.")
    scheme = urlsplit(value).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://.",
        )
    return value


def _clean_label(label: str) -> str:
    value = (label or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Label must not be empty.")
    return value[:_MAX_LABEL_LEN]


def _clean_platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if not value:
        raise HTTPException(status_code=400, detail="Platform must not be empty.")
    return value[:_MAX_PLATFORM_LEN]


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class SocialCreate(BaseModel):
    platform: str
    label: str
    url: str
    enabled: bool = True


class SocialUpdate(BaseModel):
    platform: str | None = None
    label: str | None = None
    url: str | None = None
    enabled: bool | None = None


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("")
def list_socials() -> dict[str, Any]:
    with _LOCK:
        entries = _read_entries()
    entries = sorted(entries, key=lambda e: e.get("order", 0))
    return {"entries": entries}


@router.post("", status_code=201)
def create_social(body: SocialCreate) -> dict[str, Any]:
    platform = _clean_platform(body.platform)
    label = _clean_label(body.label)
    url = _clean_url(body.url)
    with _LOCK:
        entries = _read_entries()
        next_order = max((e.get("order", 0) for e in entries), default=-1) + 1
        entry = {
            "id": uuid.uuid4().hex,
            "platform": platform,
            "label": label,
            "url": url,
            "enabled": bool(body.enabled),
            "order": next_order,
        }
        entries.append(entry)
        _write_entries(entries)
    return entry


@router.patch("/{entry_id}")
def update_social(entry_id: str, body: SocialUpdate) -> dict[str, Any]:
    with _LOCK:
        entries = _read_entries()
        target = next((e for e in entries if e.get("id") == entry_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"No social entry with id {entry_id!r}.")
        if body.platform is not None:
            target["platform"] = _clean_platform(body.platform)
        if body.label is not None:
            target["label"] = _clean_label(body.label)
        if body.url is not None:
            target["url"] = _clean_url(body.url)
        if body.enabled is not None:
            target["enabled"] = bool(body.enabled)
        _write_entries(entries)
    return target


@router.delete("/{entry_id}")
def delete_social(entry_id: str) -> dict[str, Any]:
    with _LOCK:
        entries = _read_entries()
        kept = [e for e in entries if e.get("id") != entry_id]
        removed = len(kept) != len(entries)
        if removed:
            _write_entries(kept)
    return {"ok": True, "removed": removed}
