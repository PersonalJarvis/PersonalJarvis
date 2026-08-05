"""In-app model downloads for a local Ollama server.

CLAUDE.md section 3 requires a capability to be recoverable from INSIDE the
app. The local brain card missed that bar in the one place it matters most:
with a running server and no downloads, every path failed with "run: ollama
pull <model>" — a terminal instruction in a desktop app with no terminal, and
the point where a keyless install stops being usable at all.

This module is the brain-side twin of :mod:`jarvis.speech.local_install`: a
curated shortlist of pulls, an honest size/fit verdict against the machine's
actual memory, and a background download whose progress the UI polls. The
server does the work — ``POST /api/pull`` streams NDJSON progress lines — so
nothing here needs a package manager or elevated rights.

Two deliberate choices:

* **Curated names are a starting point, never a gate.** Model names in the
  Ollama library drift (tags come and go), so any name may be pulled, and a
  name the registry does not know produces an honest "not in the library"
  message pointing at ollama.com/library rather than a silent failure.
* **Fit is advisory, never a block.** The memory verdict uses total RAM and a
  rule of thumb; a GPU box runs models the rule calls tight. It informs the
  choice — it does not forbid one (the maintainer's box is not the baseline).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from jarvis.plugins.brain.ollama import CLIENT_TIMEOUT, default_server_root, normalize_server_root

log = logging.getLogger(__name__)

PullState = Literal["idle", "running", "done", "error"]

#: Providers whose server speaks a native pull protocol. Membership is a
#: PROTOCOL fact (Ollama's ``/api/pull``), not a preference: a generic
#: OpenAI-compatible server has no standard way to be told "fetch this model",
#: which is why ``local-openai`` is absent rather than disabled. A second
#: server type joins by shipping its own puller, not by being named here.
PULL_CAPABLE_PROVIDERS: frozenset[str] = frozenset({"ollama"})

#: A pull streams gigabytes; the read timeout must cover a slow line, while the
#: connect timeout stays as short as everywhere else so a dead server fails fast.
_PULL_TIMEOUT = httpx.Timeout(connect=2.0, read=600.0, write=30.0, pool=30.0)

#: Rule of thumb for the fit verdict: weights plus runtime overhead should sit
#: inside this share of total memory. Above it the machine still runs the model,
#: just slowly (swap) — hence "tight", not "impossible".
_COMFORTABLE_SHARE = 0.6
_OVERHEAD_GB = 2.0


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    """One curated pull suggestion.

    ``size_gb`` is the approximate download size (the server reports the exact
    one during the pull). ``tools`` / ``vision`` are the capabilities the model
    line is known for — the same two gates the brain applies per turn, so the
    card can say WHY a pull matters instead of listing names.
    """

    id: str
    label: str
    size_gb: float
    purpose: str
    tools: bool = True
    vision: bool = False


#: The shortlist. Kept small on purpose — a wall of names is not a choice. One
#: general model per memory class, one multimodal, one embedder for UltraWiki,
#: because those are the three roles a local-only install actually needs.
RECOMMENDED_MODELS: tuple[RecommendedModel, ...] = (
    RecommendedModel(
        id="qwen3.5:4b",
        label="Qwen 3.5 4B",
        size_gb=2.6,
        purpose="Chat and voice on a small machine. Calls tools.",
    ),
    RecommendedModel(
        id="qwen3.5",
        label="Qwen 3.5 (default size)",
        size_gb=5.2,
        purpose="The balanced default: chat, voice, and reliable tool calling.",
    ),
    RecommendedModel(
        id="qwen3-vl",
        label="Qwen 3 VL",
        size_gb=6.0,
        purpose="Sees images — needed for Screen Context and Computer-Use.",
        vision=True,
    ),
    RecommendedModel(
        id="bge-m3",
        label="BGE-M3",
        size_gb=1.2,
        purpose="Embeddings for UltraWiki search. Not a chat model.",
        tools=False,
    ),
)


@dataclass
class _PullRun:
    """Mutable progress of ONE model download."""

    state: PullState = "idle"
    message: str = ""
    completed: int = 0
    total: int = 0
    task: asyncio.Task[None] | None = None


#: One run per model id: pulling a vision model must not look "already running"
#: because an embedder is still downloading.
_runs: dict[str, _PullRun] = {}


def _run_for(model: str) -> _PullRun:
    return _runs.setdefault(model, _PullRun())


def server_root() -> str:
    """The configured Ollama server root (override → OLLAMA_HOST → localhost)."""
    from jarvis.core import config as cfg

    endpoint = cfg.resolve_provider_endpoint(
        "ollama", vendor_default_base_url=default_server_root()
    )
    return normalize_server_root(endpoint.base_url or default_server_root())


def total_memory_gb() -> float | None:
    """Total system memory in GB, or ``None`` when it cannot be read.

    ``None`` is a real answer on a locked-down host and must stay one: the fit
    verdict then says "unknown" instead of inventing a number that would make a
    9 GB model look safe on a 4 GB box.
    """
    try:
        import psutil  # noqa: PLC0415 — lazy (AP-26)

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:  # noqa: BLE001 — an unreadable host is not an error
        log.debug("ollama-pull: total memory could not be read")
        return None


def fit_verdict(size_gb: float, memory_gb: float | None) -> tuple[str, str]:
    """``(verdict, English sentence)`` for running ``size_gb`` on this machine.

    Verdicts: ``comfortable`` | ``tight`` | ``unknown``. Never "impossible" —
    Ollama offloads to GPU memory this probe cannot see, and a hard no would be
    wrong on exactly the machines that run local models best.
    """
    if memory_gb is None:
        return "unknown", "This machine's memory could not be read."
    needed = size_gb + _OVERHEAD_GB
    if needed <= memory_gb * _COMFORTABLE_SHARE:
        return "comfortable", f"Runs comfortably in {memory_gb:g} GB of memory."
    return (
        "tight",
        f"Needs about {needed:g} GB with {memory_gb:g} GB installed — it will "
        "run, but expect it to be slow unless a GPU takes over.",
    )


async def installed_models() -> tuple[set[str], str | None]:
    """``(installed model ids, error)`` from the server's own ``/api/tags``.

    ``:cloud`` references are excluded for the same reason the brain excludes
    them: they are ollama.com-proxied, not local weights. The error string is
    ``None`` on success and an English sentence when the server did not answer
    — the caller shows it instead of an empty list that would read as "you have
    nothing installed".
    """
    root = server_root()
    try:
        async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
            resp = await client.get(f"{root}/api/tags")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — an unreachable server is a normal state
        log.info("ollama-pull: /api/tags unreachable at %s (%s)", root, type(exc).__name__)
        return set(), (
            f"Ollama did not answer at {root}. Start the server (or install it "
            "from ollama.com/download) and try again."
        )
    names = {
        str(m.get("name") or "").strip()
        for m in payload.get("models") or []
        if not str(m.get("name") or "").endswith(":cloud") and not m.get("remote")
    }
    return {n for n in names if n}, None


def _is_installed(model: str, installed: set[str]) -> bool:
    """Whether ``model`` is present, treating a bare name as its ``:latest`` tag.

    ``ollama pull qwen3.5`` installs ``qwen3.5:latest``, so a card that compared
    the two literally would offer a pull the user already completed.
    """
    if model in installed:
        return True
    if ":" not in model:
        return f"{model}:latest" in installed
    return False


async def recommendations() -> dict[str, Any]:
    """The curated shortlist, annotated with what THIS machine already has."""
    installed, error = await installed_models()
    memory_gb = total_memory_gb()
    models: list[dict[str, Any]] = []
    for entry in RECOMMENDED_MODELS:
        verdict, note = fit_verdict(entry.size_gb, memory_gb)
        models.append(
            {
                "id": entry.id,
                "label": entry.label,
                "size_gb": entry.size_gb,
                "purpose": entry.purpose,
                "tools": entry.tools,
                "vision": entry.vision,
                "installed": _is_installed(entry.id, installed),
                "fit": verdict,
                "fit_note": note,
            }
        )
    return {
        "server": server_root(),
        "server_reachable": error is None,
        "message": error or "",
        "memory_gb": memory_gb,
        "models": models,
        "installed": sorted(installed),
    }


async def _run_pull(model: str) -> None:
    """Stream ``/api/pull`` into the run state. Never raises."""
    run = _run_for(model)
    root = server_root()
    try:
        async with httpx.AsyncClient(timeout=_PULL_TIMEOUT) as client, client.stream(
            "POST", f"{root}/api/pull", json={"model": model, "stream": True}
        ) as resp:
            if resp.status_code == 404:
                run.state = "error"
                run.message = (
                    f"Ollama does not know a model called '{model}'. Check the "
                    "name at ollama.com/library and try again."
                )
                return
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    # One unparsable progress line is not a failed download —
                    # log it and keep reading, so a chatty server cannot abort
                    # a pull that is otherwise going fine.
                    log.debug("ollama-pull: unparsable progress line for %s: %r", model, line[:200])
                    continue
                if error := event.get("error"):
                    run.state = "error"
                    run.message = str(error)
                    return
                run.message = str(event.get("status") or run.message)
                run.completed = int(event.get("completed") or run.completed)
                run.total = int(event.get("total") or run.total)
    except asyncio.CancelledError:
        run.state = "error"
        run.message = f"The download of {model} was cancelled."
        raise
    except Exception as exc:  # noqa: BLE001 — surface an honest, retryable state
        run.state = "error"
        run.message = f"Could not download {model}: {exc}"
        log.info("ollama-pull: %s failed", model, exc_info=True)
        return

    # Trust the server's inventory over the stream's last line: a pull can end
    # cleanly and still leave nothing usable, and a card that says "ready" over
    # a missing model is the failure this whole area exists to prevent.
    installed, error = await installed_models()
    if error or not _is_installed(model, installed):
        run.state = "error"
        run.message = error or (
            f"The download finished, but {model} is not listed on the server. "
            "Try again — the pull may have been interrupted."
        )
        return
    run.state = "done"
    run.message = f"{model} is ready — it runs on this machine now."
    log.info("ollama-pull: %s completed", model)


async def start_pull(model: str) -> dict[str, Any]:
    """Begin (or join) a download of ``model``; returns its current state.

    Idempotent: a second call while a pull is in flight joins the running one
    instead of starting a duplicate multi-gigabyte download.
    """
    name = (model or "").strip()
    if not name:
        return {"state": "error", "model": "", "message": "No model name given."}
    installed, _error = await installed_models()
    if _is_installed(name, installed):
        return {
            "state": "done",
            "model": name,
            "already": True,
            "message": f"{name} is already installed.",
        }
    run = _run_for(name)
    if run.state == "running":
        return {"state": "running", "model": name, "message": run.message}
    run.state = "running"
    run.message = f"Starting the download of {name}…"
    run.completed = 0
    run.total = 0
    # Keep the reference on the run: a bare create_task may be garbage-collected
    # mid-download, which would look exactly like a stalled server.
    run.task = asyncio.create_task(_run_pull(name), name=f"ollama-pull-{name}")
    return {"state": "running", "model": name, "message": run.message}


async def pull_status(model: str) -> dict[str, Any]:
    """Progress of ``model``'s download plus the server's own inventory.

    The inventory wins when the two disagree: a model pulled by some other route
    (the CLI, another Jarvis window, a previous run) reads as installed even
    though this process never downloaded it.
    """
    name = (model or "").strip()
    if not name:
        return {"state": "error", "model": "", "message": "No model name given."}
    installed, error = await installed_models()
    present = _is_installed(name, installed)
    run = _run_for(name)
    state: PullState = run.state
    message = run.message
    if present and state in ("idle", "running"):
        state = "done"
        message = f"{name} is installed."
    elif state == "idle" and error:
        message = error
    percent = 0.0
    if run.total > 0:
        percent = round(min(100.0, run.completed / run.total * 100), 1)
    elif state == "done":
        percent = 100.0
    return {
        "state": state,
        "model": name,
        "message": message,
        "installed": present,
        "completed": run.completed,
        "total": run.total,
        "percent": percent,
    }


__all__ = [
    "PULL_CAPABLE_PROVIDERS",
    "RECOMMENDED_MODELS",
    "RecommendedModel",
    "fit_verdict",
    "installed_models",
    "pull_status",
    "recommendations",
    "server_root",
    "start_pull",
    "total_memory_gb",
]
