"""Start the local model server with Jarvis — when it is wanted and in use.

The Ollama brain fails fast on an unreachable server (two seconds, then the
fallback answers) and nothing used to start that server: a machine set up
for local models still began every day with a cloud answer until someone
opened the Server tab. This module is the ONE place that decides whether
the local server comes up with Jarvis, and does it:

* :func:`should_autostart` — ``[brain.providers.ollama].autostart`` is on
  (the default) AND local models are actually in use: the active brain is
  the local server, or a role (chat, voice, tools & screen, deep, embedding)
  has a configured pick. An install that never chose local models starts
  nothing and spawns nothing.
* :func:`run_once` — start an installed-but-stopped local server (never
  install it: that is a click behind the dangerous-flagged route), then load
  the chat pick with its keep-alive so the first answer does not pay the
  load time — "connects fast". A remote server is only warmed.
* :func:`schedule` — the boot task: :func:`run_once` a few seconds after
  the web server is ready, off the boot path (AP-26), never raising.
* :func:`kick` — the same run, fire-and-forget, right after the brain was
  switched to the local server, so "choose local models" means "local
  models answer" without a second click.
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

#: Live task references — a fire-and-forget task must be held somewhere or
#: the loop may drop it mid-run.
_tasks: set[asyncio.Task[None]] = set()


def _provider(cfg: Any) -> Any:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    return providers.get(SERVER_PROVIDER_ID) if isinstance(providers, dict) else None


def in_use(cfg: Any) -> tuple[bool, str]:
    """``(used, why)`` — whether anything in this install runs on the local server.

    True when the active brain is the local server, or when any writable
    role has a configured pick (the voice server's brain, the wiki's
    embedder and the deep model are local-model interfaces even while a
    cloud brain answers the chat).
    """
    from jarvis.brain.ollama_roles import WRITABLE_ROLE_IDS, current_pick  # lazy (AP-26)

    primary = str(getattr(getattr(cfg, "brain", None), "primary", "") or "").strip()
    if primary == SERVER_PROVIDER_ID:
        return True, "the active brain"
    roles: list[str] = []
    for role in WRITABLE_ROLE_IDS:
        try:
            model, _note = current_pick(cfg, role)
        except Exception:  # noqa: BLE001 — an unreadable pick is "not in use" for this decision
            log.debug("local-models autostart: pick of %s unreadable", role, exc_info=True)
            continue
        if model:
            roles.append(role)
    if roles:
        noun = "role" if len(roles) == 1 else "roles"
        return True, f"the {', '.join(roles)} {noun}"
    return False, "no role uses local models and the active brain is elsewhere"


def should_autostart(cfg: Any) -> tuple[bool, str]:
    """``(start, why)`` — the boot decision, in one sentence either way."""
    from jarvis.core.config import ollama_autostart  # lazy (AP-26)

    if not ollama_autostart(cfg):
        return False, "autostart is switched off in the Server tab"
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
