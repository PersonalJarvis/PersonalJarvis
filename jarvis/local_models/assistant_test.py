"""The end-to-end test of a local setup: does every configured role actually answer?

A role that is *configured* proves nothing — the model may have been deleted,
the server may be down, a "tools + vision" pick may declare neither. So this
runner asks the server (``probe_host``), then makes one REAL call per
configured role: a 1-token generation for the chat / deep / tools_screen
roles (through :func:`jarvis.brain.provider_test.run_provider_test`, so the
status vocabulary is the one the API-keys cards already speak), a
capability assertion on top for ``tools_screen``, an embedding round trip
for the embedding role, and — only when the managed voice server is
configured — the voice smoke probe. The report is persisted to
``DATA_DIR/state/local_models_health.json`` so the section badge and the
health monitor read the same file.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "HEALTH_FILE_NAME",
    "NOT_SET",
    "OVERALL_STATUSES",
    "RoleCheck",
    "TestReport",
    "health_path",
    "load_last_report",
    "run_setup_test",
]

#: Status of a role nobody configured — it is not an error, there is simply
#: nothing to test. Alongside :data:`PROVIDER_TEST_STATUSES` in ``status``.
NOT_SET = "not_set"

#: ``overall`` vocabulary — the same the health monitor (D7) writes.
OVERALL_STATUSES: tuple[str, ...] = ("ok", "error", "needs_setup", "unknown")

HEALTH_FILE_NAME = "local_models_health.json"

#: The generation-tested roles and the role whose pick embeds.
_GENERATION_ROLES: tuple[str, ...] = ("chat", "tools_screen", "deep")
_EMBEDDING_ROLE = "embedding"
_VOICE_ROLE = "voice"

_DEFAULT_TIMEOUT_S = 90.0
_VOICE_DEFAULT_URL = "http://127.0.0.1:8765"


@dataclass(frozen=True, slots=True)
class _OllamaSpec:
    """The three fields :func:`run_provider_test` reads from a provider spec."""

    id: str = "ollama"
    tier: str = "brain"
    auth_mode: str = "none"


@dataclass(frozen=True, slots=True)
class RoleCheck:
    """One role's verdict."""

    model: str
    #: :data:`PROVIDER_TEST_STATUSES` value or :data:`NOT_SET`.
    status: str
    latency_ms: float
    detail: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TestReport:
    """The whole run, as the panel's table and the health file show it."""

    #: Not a pytest class, whatever the name suggests.
    __test__ = False

    checked_at: str
    server: dict[str, Any]
    roles: dict[str, RoleCheck]
    #: The voice smoke probe's payload, ``None`` when no managed voice server exists.
    voice: dict[str, Any] | None
    #: One of :data:`OVERALL_STATUSES`.
    overall: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "server": dict(self.server),
            "roles": {role: check.to_payload() for role, check in self.roles.items()},
            "voice": dict(self.voice) if self.voice is not None else None,
            "overall": self.overall,
            # The badge keys the health monitor writes as well (D7), so one
            # reader serves both writers.
            "status": self.overall,
            "reason": self.reason(),
        }

    def reason(self) -> str:
        """One sentence naming the first failure, or ``""`` when all is well."""
        if not self.server.get("ok"):
            return str(self.server.get("detail") or "Ollama did not answer.")
        for role, check in self.roles.items():
            if check.status not in ("ok", NOT_SET):
                return f"{role}: {check.detail or check.status}"
        if self.voice is not None and not self.voice.get("ok", False):
            return f"voice: {self.voice.get('detail') or 'the voice probe failed'}"
        return ""


def health_path() -> Path:
    """``DATA_DIR/state/local_models_health.json`` — resolved at call time."""
    from jarvis.core import config as cfg_mod  # lazy: DATA_DIR is monkeypatched by tests

    return Path(cfg_mod.DATA_DIR) / "state" / HEALTH_FILE_NAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bool:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        log.warning("local-models: health file %s not written", path, exc_info=True)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.debug("local-models: temporary health file cleanup failed", exc_info=True)


def load_last_report() -> dict[str, Any] | None:
    """The persisted payload of the last run, or ``None`` when none / unreadable."""
    path = health_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.info("local-models: health file %s unreadable", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


# ── Reading the config ────────────────────────────────────────────────────


def _voice_launch_command(cfg: Any) -> str:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    voice = providers.get("local-realtime") if isinstance(providers, dict) else None
    return str(getattr(voice, "launch_command", "") or "")


def _voice_base_url(cfg: Any) -> str:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    voice = providers.get("local-realtime") if isinstance(providers, dict) else None
    return str(getattr(voice, "base_url", "") or "") or _VOICE_DEFAULT_URL


def _server_root(cfg: Any) -> str:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    ollama = providers.get("ollama") if isinstance(providers, dict) else None
    override = str(getattr(ollama, "base_url", "") or "")
    if override:
        from jarvis.plugins.brain.ollama import normalize_server_root

        return normalize_server_root(override)
    from jarvis.brain.ollama_pull import server_root

    return server_root()


# ── The checks ────────────────────────────────────────────────────────────


async def _generation_check(
    cfg: Any,
    model: str,
    *,
    timeout_s: float,
    brain_probe: Callable[[str, str], Awaitable[Any]] | None,
) -> RoleCheck:
    from jarvis.brain import provider_test

    result = await provider_test.run_provider_test(
        _OllamaSpec(), cfg, model=model, timeout_s=timeout_s, brain_probe=brain_probe
    )
    return RoleCheck(
        model=model,
        status=result.status,
        latency_ms=float(result.latency_ms),
        detail=result.detail or ("Answered." if result.status == "ok" else ""),
    )


async def _capability_check(root: str, check: RoleCheck, *, transport: Any) -> RoleCheck:
    """``tools_screen`` must DECLARE tools + vision, not only answer a prompt."""
    from jarvis.brain import ollama_inventory as inventory
    from jarvis.brain.provider_test import ERROR, MODEL_UNAVAILABLE

    if check.status != "ok":
        return check
    try:
        info = await inventory.get_model(root, check.model, transport=transport)
    except inventory.OllamaModelNotFound as exc:
        return RoleCheck(check.model, MODEL_UNAVAILABLE, check.latency_ms, str(exc))
    except inventory.OllamaServerError as exc:
        return RoleCheck(check.model, ERROR, check.latency_ms, str(exc))
    missing = [c for c in ("tools", "vision") if c not in info.capabilities]
    if missing:
        return RoleCheck(
            check.model,
            ERROR,
            check.latency_ms,
            f"{check.model} answers, but does not declare {' + '.join(missing)} — "
            "it cannot read the screen or call tools for Jarvis.",
        )
    return RoleCheck(check.model, "ok", check.latency_ms, "Answered; declares tools + vision.")


async def _embedding_check(root: str, model: str, *, transport: Any) -> RoleCheck:
    import time

    from jarvis.brain import ollama_inventory as inventory
    from jarvis.brain.provider_test import ERROR, MODEL_UNAVAILABLE, UNREACHABLE

    started = time.perf_counter()
    try:
        dim = await inventory.embed_probe(root, model, transport=transport)
    except inventory.OllamaModelNotFound as exc:
        return RoleCheck(model, MODEL_UNAVAILABLE, _ms(started), str(exc))
    except inventory.OllamaServerError as exc:
        text = str(exc)
        status = UNREACHABLE if "No Ollama" in text or "unreachable" in text.lower() else ERROR
        return RoleCheck(model, status, _ms(started), text)
    if dim <= 0:
        return RoleCheck(model, ERROR, _ms(started), f"{model} returned no embedding vector.")
    return RoleCheck(model, "ok", _ms(started), f"Embeds ({dim} dimensions).")


async def _voice_check(
    cfg: Any,
    *,
    timeout_s: float,
    voice_probe: Callable[[str], Awaitable[dict[str, Any]]] | None,
) -> dict[str, Any] | None:
    """The voice smoke probe — only for the Jarvis-managed server (``--model_name``)."""
    command = _voice_launch_command(cfg)
    if "--model_name" not in command:
        return None
    base_url = _voice_base_url(cfg)
    try:
        if voice_probe is not None:
            payload = await asyncio.wait_for(voice_probe(base_url), timeout=timeout_s)
        else:
            from jarvis.realtime.local_server.smoke import probe_voice_roundtrip

            payload = await probe_voice_roundtrip(base_url, timeout_s=timeout_s)
    except TimeoutError:
        return {"ok": False, "detail": f"The voice probe timed out after {timeout_s:.0f} s."}
    except Exception as exc:  # noqa: BLE001 — the report names the failure, never raises
        log.info("local-models: voice probe failed", exc_info=True)
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    out = dict(payload or {})
    out.setdefault("ok", True)
    out.setdefault("detail", "Voice round trip answered with audio.")
    return out


def _ms(started: float) -> float:
    import time

    return (time.perf_counter() - started) * 1000.0


def _overall(server: dict[str, Any], roles: dict[str, RoleCheck], voice: Any) -> str:
    configured = [c for c in roles.values() if c.status != NOT_SET]
    if not server.get("ok"):
        return "needs_setup" if not configured else "error"
    if not configured and voice is None:
        return "needs_setup"
    if any(c.status != "ok" for c in configured):
        return "error"
    if voice is not None and not voice.get("ok", False):
        return "error"
    return "ok"


# ── The runner ────────────────────────────────────────────────────────────


async def run_setup_test(
    cfg: Any,
    roles: tuple[str, ...] | None = None,
    *,
    transport: Any = None,
    brain_probe: Callable[[str, str], Awaitable[Any]] | None = None,
    voice_probe: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    persist: bool = True,
) -> TestReport:
    """Test the configured local setup end to end and persist the report.

    ``roles`` restricts the run to those writable role ids (default: all of
    them). ``transport`` is the httpx test seam for the Ollama calls;
    ``brain_probe`` / ``voice_probe`` are the seams for the generation and
    voice probes (production wiring when ``None``). Never raises for a
    failing check — every failure is a row in the report.
    """
    from jarvis.brain.ollama_roles import WRITABLE_ROLE_IDS, current_pick
    from jarvis.brain.ollama_runtime import probe_host
    from jarvis.brain.provider_test import UNREACHABLE

    wanted = tuple(roles) if roles else WRITABLE_ROLE_IDS
    unknown = [r for r in wanted if r not in WRITABLE_ROLE_IDS]
    if unknown:
        raise ValueError(f"Unknown role(s): {', '.join(unknown)}")

    root = _server_root(cfg)
    server = await probe_host(root, transport=transport)
    server["base_url"] = root

    checks: dict[str, RoleCheck] = {}
    for role in wanted:
        if role == _VOICE_ROLE:
            continue  # the voice role is judged by the smoke probe below
        model, note = current_pick(cfg, role)
        if not model:
            checks[role] = RoleCheck("", NOT_SET, 0.0, note or "No model set.")
            continue
        if not server.get("ok"):
            checks[role] = RoleCheck(model, UNREACHABLE, 0.0, str(server.get("detail") or ""))
            continue
        if role == _EMBEDDING_ROLE:
            checks[role] = await _embedding_check(root, model, transport=transport)
        elif role in _GENERATION_ROLES:
            check = await _generation_check(
                cfg, model, timeout_s=timeout_s, brain_probe=brain_probe
            )
            if role == "tools_screen":
                check = await _capability_check(root, check, transport=transport)
            checks[role] = check

    voice: dict[str, Any] | None = None
    if _VOICE_ROLE in wanted:
        voice = await _voice_check(cfg, timeout_s=timeout_s, voice_probe=voice_probe)
        voice_model, voice_note = current_pick(cfg, _VOICE_ROLE)
        if voice is None:
            checks[_VOICE_ROLE] = RoleCheck(
                voice_model, NOT_SET, 0.0, voice_note or "No managed voice server."
            )
        else:
            checks[_VOICE_ROLE] = RoleCheck(
                voice_model,
                "ok" if voice.get("ok") else "error",
                float(voice.get("first_audio_ms") or voice.get("latency_ms") or 0.0),
                str(voice.get("detail") or ""),
            )

    report = TestReport(
        checked_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        server=server,
        roles=checks,
        voice=voice,
        overall=_overall(server, checks, voice),
    )
    if persist:
        payload = report.to_payload()
        previous = load_last_report() or {}
        payload["since"] = (
            previous.get("since")
            if previous.get("status") == report.overall and previous.get("since")
            else report.checked_at
        )
        payload["last_ok"] = (
            report.checked_at if report.overall == "ok" else previous.get("last_ok")
        )
        _atomic_write_json(health_path(), payload)
    return report
