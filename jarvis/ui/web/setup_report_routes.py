"""Cross-device setup report — makes "why is THIS install different?" a 2-minute read.

The most expensive cross-device misdiagnosis (CLAUDE.md §3, device-parity
triage ritual): a feature "missing" on a second machine is usually not a code
bug but an invisible setup difference — a key that exists only on the dev box,
a tier that quietly crossed to a fallback family, a wake word never set.
This route aggregates the signals that already exist elsewhere (active tier
resolution, credential presence, section health) into ONE snapshot per device,
so two installs can be compared side by side and every difference has a name.

Share-safe by construction: the report carries key PRESENCE (booleans) only,
never key values, and no personal filesystem paths or machine names — it may
be pasted into an issue or chat without scrubbing.

* ``GET /api/setup-report``              — structured JSON
* ``GET /api/setup-report?format=text``  — human-readable plain text

Read-only and imported lazily by ``server.py`` (AP-26: nothing here runs at
boot). The section-health part reuses ``provider_routes``' cached snapshot and
is bounded by a timeout, so a cold cache degrades the report honestly
(``sections_complete: false``) instead of hanging the caller.
"""

from __future__ import annotations

import asyncio
import logging
import platform as platform_mod
import sys
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from . import provider_routes as _providers
from . import update_routes as _update

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup-report", tags=["setup-report"])

# Ceiling for the section-health portion. On a warm cache this is instant; on
# a cold one the shared snapshot task keeps computing after we stop waiting
# (``section_health`` shields it), so a retry a few seconds later completes.
_SECTION_TIMEOUT_S = 20.0


# --------------------------------------------------------------------------- #
# Snapshot builders (each fail-soft: a probe error degrades, never 500s)
# --------------------------------------------------------------------------- #
def _version() -> str:
    try:
        import jarvis

        return str(jarvis.__version__)
    except (ImportError, AttributeError):
        return "unknown"


def _platform_snapshot() -> dict[str, Any]:
    """OS/runtime facts. Deliberately no hostname — the report stays share-safe."""
    return {
        "os": sys.platform,
        "os_release": f"{platform_mod.system()} {platform_mod.release()}",
        "python": platform_mod.python_version(),
        "machine": platform_mod.machine(),
    }


def _install_snapshot() -> dict[str, Any]:
    """Managed-install marker + profile — is this a user install or a dev tree?"""
    try:
        root = _update._repo_root()
        managed = bool(root is not None and (root / _update._MARKER_NAME).exists())
        profile = _update._managed_install_profile(root) if root is not None else "unknown"
    except Exception:  # noqa: BLE001 — diagnostics must never raise
        managed, profile = False, "unknown"
    return {"managed": managed, "profile": profile}


def _behavior_snapshot(request: Request) -> dict[str, Any]:
    """Config facts that change day-to-day behavior between two devices.

    Presence booleans over raw values wherever the value is personal (the wake
    phrase names the user's assistant brand).
    """
    cfg = _providers._resolve_cfg(request)
    trigger = getattr(cfg, "trigger", None)
    wake = getattr(trigger, "wake_word", None)
    brain = getattr(cfg, "brain", None)
    phrase = str(getattr(wake, "phrase", "") or "")
    return {
        "wake_word_enabled": bool(getattr(trigger, "wake_word_enabled", False)),
        "wake_phrase_set": bool(phrase.strip()),
        "wake_engine": str(getattr(wake, "engine", "") or "unknown"),
        "reply_language": str(getattr(brain, "reply_language", "") or "auto"),
    }


def _active_tiers(request: Request) -> dict[str, str | None]:
    """The provider ACTUALLY powering each tier (post-fallback), per device."""
    return {
        "brain": _providers._active_brain(request),
        "stt": _providers._active_stt(request),
        "tts": _providers._active_tts(request),
        "realtime": _providers._active_realtime(request),
        "computer_use": _providers._active_computer_use(request),
    }


def _credential_presence(request: Request) -> dict[str, bool]:
    """Key PRESENCE per provider — strictly booleans, never a value.

    Reads every secret slot (keyring/ENV/.env), which is synchronous and slow;
    the route runs this off the event loop.
    """
    binary_path = _providers._codex_binary_path(request)
    out: dict[str, bool] = {}
    for spec in _providers.PROVIDERS:
        try:
            present = bool(
                _providers._is_credential_present(spec, binary_path if spec.id == "codex" else None)
            )
        except Exception:  # noqa: BLE001 — an unreadable slot reads as absent
            present = False
        out[spec.id] = present
    return out


async def _section_snapshot(request: Request) -> dict[str, dict[str, str]] | None:
    """Per-tier health, reason-coded — reuses the section-health cache.

    ``None`` means the probe did not complete in time (cold cache); the report
    says so honestly instead of blocking or guessing.
    """
    try:
        resp = await asyncio.wait_for(
            _providers.section_health(request, refresh=False),
            timeout=_SECTION_TIMEOUT_S,
        )
    except TimeoutError:
        return None
    except Exception as exc:  # noqa: BLE001 — diagnostics must never raise
        log.debug("setup-report: section health unavailable: %s", exc)
        return None
    return {
        name: {"status": s.status, "reason": s.reason, "detail": s.detail}
        for name, s in resp.sections.items()
    }


def _degradations(sections: dict[str, dict[str, str]] | None) -> list[str]:
    """Every tier that is NOT silently fine, with its machine-readable reason.

    This list is the whole point of the report: quiet capability degradation
    (AP-22 fallback chains) is what makes a second device "mysteriously"
    different, and here it gets named.
    """
    out: list[str] = []
    for name, section in sorted((sections or {}).items()):
        if section.get("status") in ("ok", "unknown"):
            continue
        detail = section.get("detail") or ""
        out.append(f"{name}: {section.get('reason', 'unknown')} ({detail})".strip())
    return out


def _summary(report: dict[str, Any]) -> list[str]:
    """Stable, ordered one-liners — diff two devices' summaries line by line."""
    plat = report["platform"]
    install = report["install"]
    behavior = report["behavior"]
    lines = [
        f"version: {report['version']}",
        f"platform: {plat['os_release']} | python {plat['python']} | {plat['machine']}",
        f"install: {'managed' if install['managed'] else 'dev/manual'} ({install['profile']})",
        f"wake: {'on' if behavior['wake_word_enabled'] else 'off'}"
        f" | phrase {'set' if behavior['wake_phrase_set'] else 'NOT set'}"
        f" | engine {behavior['wake_engine']}",
        f"reply language: {behavior['reply_language']}",
    ]
    for tier in sorted(report["tiers"]):
        lines.append(f"tier {tier}: {report['tiers'][tier] or '-'}")
    keyed = sorted(pid for pid, ok in report["credentials"].items() if ok)
    lines.append("keys present: " + (", ".join(keyed) if keyed else "none"))
    if not report["sections_complete"]:
        lines.append("health: probe still warming up - re-request in a few seconds")
    for entry in report["degradations"]:
        lines.append("degraded " + entry)
    return lines


def _render_text(report: dict[str, Any]) -> str:
    header = "Personal Jarvis setup report (share-safe: key presence only, never values)"
    body = "\n".join(report["summary"])
    return f"{header}\n{'=' * len(header)}\n{body}\n"


# --------------------------------------------------------------------------- #
# Route
# --------------------------------------------------------------------------- #
@router.get("")
async def get_setup_report(
    request: Request,
    fmt: str = Query(default="json", alias="format"),
) -> Any:
    """One share-safe snapshot of what powers THIS install, for device diffing."""

    def _sync_parts() -> tuple[dict[str, Any], dict[str, str | None], dict[str, bool]]:
        # Config reads, plugin-registry resolution, and keyring probes are all
        # synchronous — off the loop so the report never stalls the server.
        return (
            _behavior_snapshot(request),
            _active_tiers(request),
            _credential_presence(request),
        )

    behavior, tiers, credentials = await asyncio.to_thread(_sync_parts)
    sections = await _section_snapshot(request)

    report: dict[str, Any] = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "version": _version(),
        "platform": _platform_snapshot(),
        "install": _install_snapshot(),
        "behavior": behavior,
        "tiers": tiers,
        "credentials": credentials,
        "sections": sections or {},
        "sections_complete": sections is not None,
        "degradations": _degradations(sections),
    }
    report["summary"] = _summary(report)

    if fmt == "text":
        return PlainTextResponse(_render_text(report))
    return report


__all__ = ["router"]
