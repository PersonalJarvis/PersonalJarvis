"""A bounded, quiet self-check of the local setup — a badge, never a nag.

Started from the web server's lifespan next to the marketplace refresh
scheduler: first run ten minutes after boot, then every
``[brain.providers.ollama].health_check_hours`` (default 6). Nothing here
runs on the boot path (AP-26) and nothing toasts: the result is one record
in ``DATA_DIR/state/local_models_health.json`` (the same file the setup test
writes), which the sidebar badge and ``GET /api/providers/section-health``
read.

One check: skip entirely when the server is down AND no role is configured
(nothing to watch yet); otherwise ``probe_host`` plus one real 1-token chat
generation on the configured chat model, capped at 30 s. The clock, the
sleeper and the probes are injectable so the schedule is testable in
milliseconds with fakes.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INTERVAL_HOURS",
    "FIRST_RUN_DELAY_S",
    "GENERATION_CAP_S",
    "HealthMonitor",
    "check_once",
    "interval_hours",
    "read_health_record",
    "write_health_record",
]

FIRST_RUN_DELAY_S = 600.0
DEFAULT_INTERVAL_HOURS = 6.0
GENERATION_CAP_S = 30.0
#: Never spin faster than this, whatever the config says.
MIN_INTERVAL_S = 60.0

ProbeFn = Callable[[str], Awaitable[dict[str, Any]]]
GenerateFn = Callable[[Any, str], Awaitable[Any]]


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


def read_health_record() -> dict[str, Any]:
    """The badge record: ``{status, reason, since, last_ok, checked_at}`` (file only)."""
    from jarvis.local_models.assistant_test import load_last_report

    payload = load_last_report() or {}
    return {
        "status": str(payload.get("status") or "unknown"),
        "reason": str(payload.get("reason") or ""),
        "since": payload.get("since"),
        "last_ok": payload.get("last_ok"),
        "checked_at": payload.get("checked_at"),
    }


def write_health_record(status: str, reason: str, *, checked_at: str | None = None) -> bool:
    """Merge the badge keys into the health file, keeping the last test's table."""
    from jarvis.local_models.assistant_test import (
        _atomic_write_json,
        health_path,
        load_last_report,
    )

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
    from jarvis.local_models.assistant_test import _OllamaSpec

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
    from jarvis.local_models.assistant_test import _server_root

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
