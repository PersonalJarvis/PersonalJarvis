"""Start the local model server with Jarvis — when it is wanted and in use.

The Ollama brain fails fast on an unreachable server (two seconds, then the
fallback answers) and nothing used to start that server: a machine set up
for local models still began every day with a cloud answer until someone
opened the Server tab. This module is the ONE place that decides whether
the local server comes up with Jarvis, and does it:

* :func:`should_autostart` — ``[brain.providers.ollama].autostart`` is on
  (the default) AND local models are actually SELECTED: the active brain is
  the local server, or the active realtime provider is a local one. An
  install that never chose local models starts nothing and spawns nothing.
* :func:`run_once` — start an installed-but-stopped local server (never
  install it: that is a click behind the dangerous-flagged route), then load
  the chat pick with its keep-alive so the first answer does not pay the
  load time — "connects fast". A remote server is only warmed.
* :func:`schedule` — the boot task: :func:`run_once` a few seconds after
  the web server is ready, off the boot path (AP-26), never raising.
* :func:`kick` — the same run, fire-and-forget, right after the brain was
  switched to the local server, so "choose local models" means "local
  models answer" without a second click.
* :func:`release` — its mirror, right after the brain was switched AWAY:
  unload what is resident and stop the server this install started, so
  "choose a hosted provider" also means "give the accelerator back".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "BOOT_DELAY_S",
    "DEFAULT_KEEP_ALIVE",
    "SERVER_PROVIDER_ID",
    "in_use",
    "kick",
    "release",
    "release_once",
    "run_once",
    "schedule",
    "should_autostart",
]

#: The provider card whose server this module starts. The Local models
#: section is gated on the ``supports_model_pull`` capability; today exactly
#: one card declares it, and its runtime (``ollama serve``) is the one
#: :mod:`jarvis.brain.ollama_runtime` knows how to spawn — hence an id here
#: rather than a capability probe, spelled out once.
SERVER_PROVIDER_ID = "ollama"

#: Seconds after the web server is ready before the boot run starts. Long
#: enough for the ready signal and the first paint to be out of the way.
BOOT_DELAY_S = 5.0

#: Residency the warm ping asks for when the chat pick has no keep-alive of
#: its own (a Go duration, what Ollama's own default is anyway).
DEFAULT_KEEP_ALIVE = "30m"

StatusFn = Callable[[], dict[str, object]]
StartFn = Callable[[], tuple[bool, str]]
WarmFn = Callable[[str, str, str | int], Awaitable[bool]]
RunFn = Callable[[Any], Awaitable[dict[str, Any]]]
StopFn = Callable[[], tuple[bool, str]]
UnloadFn = Callable[[str], Awaitable[list[str]]]
ReleaseFn = Callable[[Any], Awaitable[dict[str, Any]]]
VoiceStopFn = Callable[[], Awaitable[bool]]

#: Live task references — a fire-and-forget task must be held somewhere or
#: the loop may drop it mid-run.
_tasks: set[asyncio.Task[None]] = set()


def _provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get(SERVER_PROVIDER_ID) if isinstance(providers, dict) else None


def _active_local_realtime(cfg: Any) -> str:
    """The active realtime provider id when it is a LOCAL card, else ``""``.

    A local realtime card runs its speech stack on this machine and takes
    its answers from the local model server, so that server belongs up
    while it is the SELECTED voice — a fallback entry does not count, only
    what is live. Spec-derived through ``LOCAL_PROVIDERS``
    (``auth_mode == "none"``), never a hand-written name list (AP-21).
    """
    realtime = getattr(getattr(cfg, "brain", None), "realtime", None)
    provider = str(getattr(realtime, "provider", "") or "").strip()
    if not provider:
        return ""
    try:
        from jarvis.brain.app_control import LOCAL_PROVIDERS  # lazy (AP-26)
    except Exception:  # noqa: BLE001 — no spec table: fall back to "not a local voice"
        log.debug("local-models: provider spec unreadable for the realtime check", exc_info=True)
        return ""
    return provider if provider in LOCAL_PROVIDERS else ""


def in_use(cfg: Any) -> tuple[bool, str]:
    """``(used, why)`` — whether an ACTIVE choice runs on the local server.

    Selected, not merely configured. Two things count: the active brain is
    the local server, or the active realtime provider is a local card whose
    speech stack answers from this machine's model server.

    A writable role that merely HOLDS a model name does NOT count, and that
    is the whole point (BUG-204). The Ollama card keeps its picks while a
    hosted provider answers — switching the brain to a cloud card left
    ``chat`` pointing at a local tag, this returned "in use" forever, and a
    multi-GB model stayed resident on the accelerator behind its keep-alive
    for as long as Jarvis ran. Picking a provider is the switch; a stored
    name is not. A role that needs the server while nothing selects it
    starts it on demand instead of holding it warm all day.
    """
    primary = str(getattr(getattr(cfg, "brain", None), "primary", "") or "").strip()
    if primary == SERVER_PROVIDER_ID:
        return True, "the active brain"
    realtime = _active_local_realtime(cfg)
    if realtime:
        return True, f"the active realtime voice ({realtime})"
    return False, "neither the active brain nor the active voice runs on local models"


def should_autostart(cfg: Any) -> tuple[bool, str]:
    """``(start, why)`` — the boot decision, in one sentence either way.

    The off sentence names the one case where "off" cannot mean "gone": the
    active brain or voice is itself the local server, so switching local
    models off would leave Jarvis with nothing to answer on. The user reads
    that instead of watching a model stay resident with no explanation.
    """
    from jarvis.core.config import ollama_autostart  # lazy (AP-26)

    if not ollama_autostart(cfg):
        used, why = in_use(cfg)
        if used:
            return False, f"local models are switched off, but {why} still runs on them"
        return False, "local models are switched off"
    used, why = in_use(cfg)
    if not used:
        return False, why
    return True, f"local models serve {why}"


def _chat_model(cfg: Any) -> str:
    """The pick worth warming: chat, else deep, else tools & screen."""
    from jarvis.brain.ollama_roles import current_pick  # lazy (AP-26)

    for role in ("chat", "deep", "tools_screen"):
        try:
            model, _note = current_pick(cfg, role)
        except Exception:  # noqa: BLE001 — an unreadable pick is simply not warmed
            log.debug("local-models autostart: pick of %s unreadable", role, exc_info=True)
            continue
        if model:
            return model
    return ""


def _keep_alive(cfg: Any, model: str) -> str | int:
    provider = _provider(cfg)
    models = getattr(provider, "models", None) if provider is not None else None
    opts = models.get(model) if isinstance(models, dict) else None
    keep_alive = getattr(opts, "keep_alive", None)
    return keep_alive if keep_alive is not None else DEFAULT_KEEP_ALIVE


async def _default_unload(root: str) -> list[str]:
    """Unload every model resident on ``root``; returns the names freed.

    Aliases occupy their own memory, so ``/api/ps`` is the list that matters
    rather than the configured picks. One failed unload does not stop the
    others — the stop that follows frees the rest anyway.
    """
    from jarvis.brain import ollama_inventory  # lazy (AP-26)

    freed: list[str] = []
    for running in await ollama_inventory.running_models(root):
        try:
            await ollama_inventory.unload_model(root, running.name)
        except Exception as exc:  # noqa: BLE001 — one stubborn model must not keep the rest resident
            log.debug("local-models: unloading %s failed (%s)", running.name, exc, exc_info=True)
            continue
        freed.append(running.name)
    return freed


async def _default_warm(root: str, model: str, keep_alive: str | int) -> bool:
    from jarvis.brain.ollama_profiles import warm  # lazy (AP-26)

    return await warm(root, model, keep_alive)


async def run_once(
    cfg: Any,
    *,
    status: StatusFn | None = None,
    start: StartFn | None = None,
    warm: WarmFn | None = None,
) -> dict[str, Any]:
    """Start the server when it is installed and stopped, then warm the chat pick.

    Returns ``{"started", "warmed", "detail"}`` — ``started`` when this run
    spawned the server, ``warmed`` the model now resident (``""`` when none),
    ``detail`` the runtime's own sentence. The probes are injectable; never
    raises on a server that will not come up — the sentence says why.
    """
    from jarvis.brain import ollama_runtime  # lazy (AP-26)
    from jarvis.local_models.health_monitor import server_root  # lazy (AP-26)

    record: dict[str, Any] = {"started": False, "warmed": "", "detail": ""}
    root = server_root(cfg)
    state = await asyncio.to_thread(status or ollama_runtime.runtime_status)
    if not state.get("running"):
        if ollama_runtime.host_kind(root) == "remote":
            record["detail"] = (
                f"The server at {root} is remote and does not answer; nothing to start here."
            )
            return record
        if not state.get("installed"):
            record["detail"] = "Ollama is not installed; install it from the Server tab."
            return record
        ok, detail = await asyncio.to_thread(start or ollama_runtime.start_server)
        record["detail"] = detail
        if not ok:
            return record
        record["started"] = True
    model = _chat_model(cfg)
    if model and await (warm or _default_warm)(root, model, _keep_alive(cfg, model)):
        record["warmed"] = model
    return record


def _remember(task: asyncio.Task[None]) -> asyncio.Task[None]:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def schedule(
    cfg_fn: Callable[[], Any],
    *,
    delay_s: float = BOOT_DELAY_S,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    run: RunFn | None = None,
) -> asyncio.Task[None]:
    """The boot task: wait ``delay_s``, decide, run. Logs; never raises."""

    async def _boot() -> None:
        await (sleep or asyncio.sleep)(delay_s)
        try:
            cfg = cfg_fn()
            wanted, why = should_autostart(cfg)
            if not wanted:
                log.info("local-models autostart: skipped — %s.", why)
                return
            record = await (run or run_once)(cfg)
            log.info(
                "local-models autostart (%s): started=%s warmed=%r %s",
                why,
                record.get("started"),
                record.get("warmed"),
                record.get("detail") or "",
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed autostart is a log line, never a boot error
            log.warning("local-models autostart failed", exc_info=True)

    return _remember(asyncio.create_task(_boot(), name="local-models-autostart"))


def kick(cfg: Any, *, run: RunFn | None = None) -> asyncio.Task[None] | None:
    """After a switch to the local server: start and warm now, fire-and-forget.

    ``None`` outside a running loop (the CLI's synchronous callers) — the
    boot task covers the next start, and the first turn still starts the
    server itself through the runtime revive.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _now() -> None:
        try:
            record = await (run or run_once)(cfg)
            log.info(
                "local-models: server readied after the brain switch — started=%s warmed=%r %s",
                record.get("started"),
                record.get("warmed"),
                record.get("detail") or "",
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the switch already landed; this only delays the first answer
            log.warning("local-models: readying the server after the switch failed", exc_info=True)

    return _remember(loop.create_task(_now(), name="local-models-kick"))


async def _stop_local_voice_server() -> bool:
    """Stop the managed local speech server, if this install is running one.

    It is a local model too — the biggest one on the card — and it holds the
    model server open as its brain, so releasing the accelerator without it
    frees nothing. Only ever the process this install owns
    (``owned_only=True``); a foreign one is left alone.
    """
    try:
        from jarvis.realtime.local_server import supervisor  # lazy (AP-26)

        ok, detail = await asyncio.to_thread(supervisor.stop, owned_only=True)
    except Exception as exc:  # noqa: BLE001 — no managed voice server is the normal case
        log.debug("local-models: managed voice server not stopped (%s)", exc, exc_info=True)
        return False
    if ok:
        log.info("local-models: managed voice server stopped — %s", detail)
    return bool(ok)


async def release_once(
    cfg: Any,
    *,
    unload: UnloadFn | None = None,
    stop: StopFn | None = None,
    voice_stop: VoiceStopFn | None = None,
) -> dict[str, Any]:
    """Hand the accelerator back: stop the local voice stack and our server.

    Returns ``{"stopped", "unloaded", "voice_stopped", "detail"}`` — the
    models freed, whether the server this install spawned was stopped, and
    whether a managed voice server went with it. Refuses while
    :func:`in_use` still holds (a local voice may keep answering on a hosted
    brain), and the runtime only ever stops the pid Jarvis recorded, so a
    server the user started from the tray or a terminal is left alone.
    Never raises.
    """
    from jarvis.brain import ollama_runtime  # lazy (AP-26)
    from jarvis.local_models.health_monitor import server_root  # lazy (AP-26)

    record: dict[str, Any] = {
        "stopped": False,
        "unloaded": [],
        "voice_stopped": False,
        "detail": "",
    }
    used, why = in_use(cfg)
    if used:
        record["detail"] = f"kept running — {why} still uses it"
        return record
    record["voice_stopped"] = await (voice_stop or _stop_local_voice_server)()
    root = server_root(cfg)
    if ollama_runtime.host_kind(root) == "remote":
        record["detail"] = f"The server at {root} is remote; this install does not stop it."
        return record
    try:
        record["unloaded"] = await (unload or _default_unload)(root)
    except Exception as exc:  # noqa: BLE001 — a server already down needs no unload
        log.debug("local-models: unload before stop skipped (%s)", exc, exc_info=True)
    ok, detail = await asyncio.to_thread(stop or ollama_runtime.stop_server)
    record["stopped"] = ok
    record["detail"] = detail
    return record


def release(cfg: Any, *, run: ReleaseFn | None = None) -> asyncio.Task[None] | None:
    """After a switch AWAY from the local server: free it, fire-and-forget.

    The mirror of :func:`kick`, and the reason picking a hosted provider is
    enough to get the graphics memory back — before this, a switch away
    started nothing and stopped nothing, so the model stayed resident
    behind its keep-alive until Jarvis quit (BUG-204). ``None`` outside a
    running loop, exactly like :func:`kick`.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _now() -> None:
        try:
            record = await (run or release_once)(cfg)
            log.info(
                "local-models: released — stopped=%s voice_stopped=%s unloaded=%r %s",
                record.get("stopped"),
                record.get("voice_stopped"),
                record.get("unloaded"),
                record.get("detail") or "",
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the switch already landed; only the memory stays held
            log.warning("local-models: releasing the server after the switch failed", exc_info=True)

    return _remember(loop.create_task(_now(), name="local-models-release"))
