"""A bounded, quiet self-check of the local setup — a badge, never a nag.

Started from the web server's lifespan next to the marketplace refresh
scheduler: first run ten minutes after boot, then every
``[brain.providers.ollama].health_check_hours`` (default 6). Nothing here
runs on the boot path (AP-26) and nothing toasts: the result is one record
in ``DATA_DIR/state/local_models_health.json``, which the sidebar badge and
``GET /api/providers/section-health`` read.

One check: skip entirely when the server is down AND no role is configured
(nothing to watch yet); otherwise ``probe_host`` plus one real 1-token chat
generation on the configured chat model, capped at 30 s. The clock, the
sleeper and the probes are injectable so the schedule is testable in
milliseconds with fakes.
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
    "DEFAULT_INTERVAL_HOURS",
    "FIRST_RUN_DELAY_S",
    "GENERATION_CAP_S",
    "HEALTH_FILE_NAME",
    "HealthMonitor",
    "check_once",
    "health_path",
    "interval_hours",
    "load_last_report",
    "read_health_record",
    "server_root",
    "verify_setup",
    "write_health_record",
]

FIRST_RUN_DELAY_S = 600.0
DEFAULT_INTERVAL_HOURS = 6.0
GENERATION_CAP_S = 30.0
#: Never spin faster than this, whatever the config says.
MIN_INTERVAL_S = 60.0

HEALTH_FILE_NAME = "local_models_health.json"

ProbeFn = Callable[[str], Awaitable[dict[str, Any]]]
GenerateFn = Callable[[Any, str], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class _OllamaSpec:
    """The three fields :func:`run_provider_test` reads from a provider spec."""

    id: str = "ollama"
    tier: str = "brain"
    auth_mode: str = "none"


# ── config + record ───────────────────────────────────────────────────────


def interval_hours(cfg: Any) -> float:
    """``[brain.providers.ollama].health_check_hours`` — the ONE reader (AP-31)."""
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    ollama = providers.get("ollama") if isinstance(providers, dict) else None
    try:
        hours = float(getattr(ollama, "health_check_hours", DEFAULT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS
    return hours if hours > 0 else DEFAULT_INTERVAL_HOURS


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
    """The persisted payload of the last check, or ``None`` when none / unreadable."""
    path = health_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.info("local-models: health file %s unreadable", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def read_health_record() -> dict[str, Any]:
    """The badge record: ``{status, reason, since, last_ok, checked_at}`` (file only)."""
    payload = load_last_report() or {}
    return {
        "status": str(payload.get("status") or "unknown"),
        "reason": str(payload.get("reason") or ""),
        "since": payload.get("since"),
        "last_ok": payload.get("last_ok"),
        "checked_at": payload.get("checked_at"),
    }


def write_health_record(status: str, reason: str, *, checked_at: str | None = None) -> bool:
    """Merge the badge keys into the health file; ``since`` holds while the status does."""
    stamp = checked_at or _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    previous = load_last_report() or {}
    payload = dict(previous)
    payload["status"] = status
    payload["reason"] = reason
    payload["checked_at"] = stamp
    payload["since"] = (
        previous.get("since")
        if previous.get("status") == status and previous.get("since")
        else stamp
    )
    payload["last_ok"] = stamp if status == "ok" else previous.get("last_ok")
    payload["monitor"] = {"checked_at": stamp, "status": status, "reason": reason}
    return _atomic_write_json(health_path(), payload)


# ── one check ─────────────────────────────────────────────────────────────


def server_root(cfg: Any) -> str:
    """The local server's root: the card's ``base_url`` override, else the default."""
    return _server_root(cfg)


def _server_root(cfg: Any) -> str:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    ollama = providers.get("ollama") if isinstance(providers, dict) else None
    override = str(getattr(ollama, "base_url", "") or "")
    if override:
        from jarvis.plugins.brain.ollama import normalize_server_root

        return normalize_server_root(override)
    from jarvis.brain.ollama_pull import server_root

    return server_root()


def _configured_roles(cfg: Any) -> dict[str, str]:
    from jarvis.brain.ollama_roles import WRITABLE_ROLE_IDS, current_pick

    out: dict[str, str] = {}
    for role in WRITABLE_ROLE_IDS:
        try:
            model, _note = current_pick(cfg, role)
        except Exception:  # noqa: BLE001 — a broken pick is "not configured" for the badge
            log.debug("health monitor: pick of %s unreadable", role, exc_info=True)
            continue
        if model:
            out[role] = model
    return out


async def _default_probe(root: str) -> dict[str, Any]:
    from jarvis.brain.ollama_runtime import probe_host

    return await probe_host(root)


async def _default_generate(cfg: Any, model: str) -> Any:
    from jarvis.brain import provider_test

    return await provider_test.run_provider_test(
        _OllamaSpec(), cfg, model=model, timeout_s=GENERATION_CAP_S
    )


async def check_once(
    cfg: Any,
    *,
    root: str | None = None,
    probe: ProbeFn | None = None,
    generate: GenerateFn | None = None,
    persist: bool = True,
) -> dict[str, Any] | None:
    """One self-check; returns the record written, or ``None`` when skipped."""
    root = root or _server_root(cfg)
    roles = _configured_roles(cfg)
    server = await (probe or _default_probe)(root)
    if not server.get("ok") and not roles:
        log.debug("health monitor: server down and nothing configured — skipped")
        return None
    if not server.get("ok"):
        status, reason = "error", str(server.get("detail") or "Ollama did not answer.")
    elif not roles:
        status, reason = "needs_setup", "Ollama runs, but no role is configured."
    else:
        model = roles.get("chat") or roles.get("deep") or roles.get("tools_screen") or ""
        if not model:
            status, reason = "ok", ""
        else:
            try:
                result = await asyncio.wait_for(
                    (generate or _default_generate)(cfg, model), timeout=GENERATION_CAP_S
                )
            except TimeoutError:
                result = None
                status, reason = "error", f"{model} did not answer within {GENERATION_CAP_S:.0f} s."
            except Exception as exc:  # noqa: BLE001 — the badge says so, the log keeps it
                log.info("health monitor: generation on %s failed", model, exc_info=True)
                result = None
                status, reason = "error", f"{model}: {type(exc).__name__}: {exc}"
            if result is not None:
                if getattr(result, "status", "") == "ok":
                    status, reason = "ok", ""
                else:
                    detail = getattr(result, "detail", "") or getattr(result, "status", "error")
                    status, reason = "error", f"{model}: {detail}"
    record = {"status": status, "reason": reason}
    if persist:
        write_health_record(status, reason)
    return record


# ── the on-demand proof ───────────────────────────────────────────────────

EmbedFn = Callable[[str, str], Awaitable[int]]


async def _default_embed(root: str, model: str) -> int:
    from jarvis.brain.ollama_inventory import embed_probe

    return await embed_probe(root, model)


def _step(
    step_id: str, ok: bool | None, *, model: str = "", detail: str = "", ms: int = 0
) -> dict[str, Any]:
    return {"id": step_id, "ok": ok, "model": model, "detail": detail, "ms": ms}


async def verify_setup(
    cfg: Any,
    *,
    root: str | None = None,
    probe: ProbeFn | None = None,
    generate: GenerateFn | None = None,
    embed: EmbedFn | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Prove the setup works, step by step, and write the health record.

    :func:`check_once` is the quiet schedule; this is the answer to a click
    ("Set up everything", "Run a check"). Three steps, each with how long it
    took: the server answers, the chat pick produces one real answer, the
    embedding pick produces one real vector. A step whose role is not
    configured is reported as not run (``ok: None``) rather than as a pass,
    so "it is set up" is a sentence that was tested, not assumed. Returns
    ``{"ok", "status", "reason", "steps"}``; ``status`` uses the badge's
    vocabulary (``ok`` / ``needs_setup`` / ``error``).
    """
    import time

    root = root or _server_root(cfg)
    roles = _configured_roles(cfg)
    steps: list[dict[str, Any]] = []

    server = await (probe or _default_probe)(root)
    server_ok = bool(server.get("ok"))
    steps.append(
        _step(
            "server",
            server_ok,
            detail=str(server.get("detail") or "")
            if not server_ok
            else f"Ollama {server.get('version') or 'unknown'}",
            ms=int(server.get("latency_ms") or 0),
        )
    )

    chat_model = roles.get("chat") or roles.get("deep") or roles.get("tools_screen") or ""
    embed_model = roles.get("embedding") or ""
    not_run = "Not tested — the server did not answer."
    if not server_ok:
        steps.append(_step("chat", None, model=chat_model, detail=not_run))
        steps.append(_step("embedding", None, model=embed_model, detail=not_run))
    else:
        if chat_model:
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    (generate or _default_generate)(cfg, chat_model), timeout=GENERATION_CAP_S
                )
                ms = int((time.monotonic() - started) * 1000)
                if getattr(result, "status", "") == "ok":
                    steps.append(_step("chat", True, model=chat_model, detail="Answered.", ms=ms))
                else:
                    detail = getattr(result, "detail", "") or getattr(result, "status", "error")
                    steps.append(_step("chat", False, model=chat_model, detail=str(detail), ms=ms))
            except TimeoutError:
                steps.append(
                    _step(
                        "chat",
                        False,
                        model=chat_model,
                        detail=f"No answer within {GENERATION_CAP_S:.0f} s.",
                        ms=int(GENERATION_CAP_S * 1000),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — the step says so, the log keeps it
                log.info("verify: generation on %s failed", chat_model, exc_info=True)
                steps.append(
                    _step("chat", False, model=chat_model, detail=f"{type(exc).__name__}: {exc}")
                )
        else:
            steps.append(_step("chat", None, detail="No chat role is configured."))
        if embed_model:
            started = time.monotonic()
            try:
                dims = await asyncio.wait_for(
                    (embed or _default_embed)(root, embed_model), timeout=GENERATION_CAP_S
                )
                ms = int((time.monotonic() - started) * 1000)
                if dims > 0:
                    steps.append(
                        _step(
                            "embedding",
                            True,
                            model=embed_model,
                            detail=f"{dims} dimensions.",
                            ms=ms,
                        )
                    )
                else:
                    steps.append(
                        _step(
                            "embedding",
                            False,
                            model=embed_model,
                            detail="The server answered without a vector.",
                            ms=ms,
                        )
                    )
            except TimeoutError:
                steps.append(
                    _step(
                        "embedding",
                        False,
                        model=embed_model,
                        detail=f"No vector within {GENERATION_CAP_S:.0f} s.",
                    )
                )
            except Exception as exc:  # noqa: BLE001 — same as the chat step
                log.info("verify: embedding on %s failed", embed_model, exc_info=True)
                steps.append(_step("embedding", False, model=embed_model, detail=str(exc)))
        else:
            steps.append(_step("embedding", None, detail="No embedding role is configured."))

    failed = next((s for s in steps if s["ok"] is False), None)
    if failed is not None:
        status = "error"
        who = failed["model"] or "The server"
        reason = f"{who}: {failed['detail']}" if failed["detail"] else f"{who} failed."
    elif not roles:
        status, reason = "needs_setup", "Ollama runs, but no role is configured."
    else:
        status, reason = "ok", ""
    if persist:
        write_health_record(status, reason)
    return {"ok": status == "ok", "status": status, "reason": reason, "steps": steps}


# ── the schedule ──────────────────────────────────────────────────────────


class HealthMonitor:
    """The periodic task: ``start()`` once after boot, ``stop()`` on shutdown."""

    def __init__(
        self,
        cfg_fn: Callable[[], Any],
        *,
        first_delay_s: float = FIRST_RUN_DELAY_S,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        check: Callable[[Any], Awaitable[Any]] | None = None,
    ) -> None:
        self._cfg_fn = cfg_fn
        self._first_delay_s = first_delay_s
        self._sleep = sleep or asyncio.sleep
        self._check = check or check_once
        self._task: asyncio.Task[None] | None = None
        self.runs = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name="local-models-health-monitor")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — shutdown stays best-effort
            log.debug("health monitor: task ended with an error on stop", exc_info=True)

    def _interval_s(self) -> float:
        try:
            return max(MIN_INTERVAL_S, interval_hours(self._cfg_fn()) * 3600.0)
        except Exception:  # noqa: BLE001 — a broken config keeps the default cadence
            log.debug("health monitor: interval unreadable, using the default", exc_info=True)
            return DEFAULT_INTERVAL_HOURS * 3600.0

    async def _loop(self) -> None:
        await self._sleep(self._first_delay_s)
        while True:
            try:
                await self._check(self._cfg_fn())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one failed check never ends the schedule
                log.warning("health monitor: check failed", exc_info=True)
            self.runs += 1
            await self._sleep(self._interval_s())
