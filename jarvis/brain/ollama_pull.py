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


#: The jobs a local install has to fill. Each one is a separate decision — a
#: machine needs a chat model AND an embedder, they do not substitute for each
#: other — so the shortlist offers a best pick per role rather than one ranked
#: list where the embedder looks like a weak chat model.
Role = Literal["chat", "vision", "coder", "embedding"]

#: Display order of the roles, most-load-bearing first.
ROLE_ORDER: tuple[Role, ...] = ("chat", "vision", "coder", "embedding")


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    """One curated pull suggestion.

    ``size_gb`` is the fallback download size, used only until the registry
    answers with the real one (see :func:`registry_size_gb`). ``tools`` /
    ``vision`` are the capabilities the model line is known for — the same two
    gates the brain applies per turn, so the card can say WHY a pull matters
    instead of listing names.
    """

    id: str
    label: str
    size_gb: float
    purpose: str
    role: Role = "chat"
    tools: bool = True
    vision: bool = False


#: The shortlist, spread across memory classes rather than pinned to one.
#:
#: The old list held one small chat model, one mid one, one multimodal and one
#: embedder — the same four names on a 8 GB laptop and on a workstation with a
#: 48 GB card, so the workstation was told to run a 4B model and the laptop was
#: shown nothing it could not also have found itself. The classes below exist so
#: :func:`recommendations` can pick the LARGEST entry per role that this
#: particular machine runs well, which is the whole point of a recommendation.
#:
#: Every id here is verified to exist in the Ollama library (see
#: ``tests/integration/test_ollama_catalog_is_current.py``, which asks the
#: registry). That test is also the guard against the list going stale: a tag
#: that is retired fails it rather than reaching a user as "Ollama does not know
#: a model called …". Sizes are the registry's own, refreshed at request time.
RECOMMENDED_MODELS: tuple[RecommendedModel, ...] = (
    # ── chat / voice / tools ────────────────────────────────────────────────
    RecommendedModel(
        id="qwen3.5:2b",
        label="Qwen 3.5 2B",
        size_gb=2.7,
        purpose="Chat and voice on a small laptop. Calls tools.",
    ),
    RecommendedModel(
        id="qwen3.5:4b",
        label="Qwen 3.5 4B",
        size_gb=3.4,
        purpose="Chat and voice with more headroom, still light on memory.",
    ),
    RecommendedModel(
        id="qwen3.5",
        label="Qwen 3.5 9B",
        size_gb=6.6,
        purpose="The balanced default: chat, voice, and reliable tool calling.",
    ),
    RecommendedModel(
        id="gemma4:12b-it-qat",
        label="Gemma 4 12B QAT",
        size_gb=7.2,
        purpose="Current quantized 12B chat, tools, and vision for one GPU.",
    ),
    RecommendedModel(
        id="gemma4:26b-a4b-it-qat",
        label="Gemma 4 26B A4B QAT",
        size_gb=16.0,
        purpose="Current sparse Gemma with stronger reasoning where memory allows.",
    ),
    # ── vision (Screen Context / Computer-Use) ──────────────────────────────
    RecommendedModel(
        id="qwen3-vl:2b",
        label="Qwen 3 VL 2B",
        size_gb=1.9,
        purpose="Sees your screen on a small machine.",
        role="vision",
        vision=True,
    ),
    RecommendedModel(
        id="qwen3-vl:4b",
        label="Qwen 3 VL 4B",
        size_gb=3.3,
        purpose="Sees your screen, with more detail than the 2B.",
        role="vision",
        vision=True,
    ),
    RecommendedModel(
        id="qwen3-vl",
        label="Qwen 3 VL 8B",
        size_gb=6.1,
        purpose="The balanced choice for Screen Context and Computer-Use.",
        role="vision",
        vision=True,
    ),
    RecommendedModel(
        id="qwen3-vl:32b",
        label="Qwen 3 VL 32B",
        size_gb=20.9,
        purpose="Reads dense screens and small text most reliably.",
        role="vision",
        vision=True,
    ),
    # ── coding worker (the Agents tab's heavy-task model) ───────────────────
    RecommendedModel(
        id="qwen3.6:27b",
        label="Qwen 3.6 27B",
        size_gb=17.0,
        purpose="Current dense Qwen for agentic coding and repository work.",
        role="coder",
    ),
    RecommendedModel(
        id="qwen3.6:35b-a3b",
        label="Qwen 3.6 35B A3B",
        size_gb=24.0,
        purpose="Current sparse Qwen coding worker; 3B parameters activate per token.",
        role="coder",
    ),
    # ── embeddings (UltraWiki search) ───────────────────────────────────────
    RecommendedModel(
        id="embeddinggemma",
        label="EmbeddingGemma",
        size_gb=0.6,
        purpose="Embeddings for search on any machine. Not a chat model.",
        role="embedding",
        tools=False,
    ),
    RecommendedModel(
        id="qwen3-embedding:4b",
        label="Qwen 3 Embedding 4B",
        size_gb=2.5,
        purpose="Higher-capacity current multilingual embeddings for retrieval.",
        role="embedding",
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
        from jarvis.hardware.detection import system_ram_gb  # noqa: PLC0415 — lazy (AP-26)

        return system_ram_gb()
    except Exception:  # noqa: BLE001 — an unreadable host is not an error
        log.debug("ollama-pull: total memory could not be read")
        return None


def accelerator_gb() -> tuple[float, str]:
    """``(GiB, source)`` of GPU memory this machine can give a model.

    ``(0.0, "none")`` means "no accelerator I can vouch for" — a CPU-only box,
    but equally an AMD or Intel card this probe cannot read. It is never read as
    "cannot run models": system RAM still runs them, just slower, which is
    exactly what :func:`fit_verdict` then says.
    """
    try:
        from jarvis.hardware.detection import (  # noqa: PLC0415 — lazy (AP-26)
            usable_accelerator_gb,
        )

        return usable_accelerator_gb()
    except Exception:  # noqa: BLE001 — a probe failure is not an error state
        log.debug("ollama-pull: accelerator probe unavailable", exc_info=True)
        return 0.0, "none"


def fit_verdict(
    size_gb: float,
    memory_gb: float | None,
    accel_gb: float = 0.0,
) -> tuple[str, str]:
    """``(verdict, English sentence)`` for running ``size_gb`` on this machine.

    Verdicts: ``comfortable`` | ``tight`` | ``unknown``. Never "impossible" —
    a hard no would be wrong on exactly the machines that run local models best.

    GPU memory decides first when it is known. The RAM rule alone called a 14 GB
    model "tight" on a box with a 24 GB card that runs it at full speed, and
    "comfortable" on a 64 GB CPU-only server where it crawls — both backwards.
    Whichever memory answers, the sentence names it, so the verdict can be
    checked against the machine rather than taken on faith.
    """
    needed = size_gb + _OVERHEAD_GB
    if accel_gb > 0:
        if needed <= accel_gb:
            return (
                "comfortable",
                f"Fits in the {accel_gb:.0f} GB of graphics memory on this "
                "machine — it will run at full speed.",
            )
        if memory_gb is not None and needed <= memory_gb * _COMFORTABLE_SHARE:
            return (
                "tight",
                f"Bigger than the {accel_gb:.0f} GB of graphics memory, so part "
                f"of it runs on the CPU out of {memory_gb:g} GB of RAM. It "
                "works, just slower.",
            )
    if memory_gb is None:
        return "unknown", "This machine's memory could not be read."
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


#: Real download sizes from the public Ollama registry, cached for the process.
#: Sizes drift as a tag is re-quantised, and the hardcoded estimates were up to
#: a third off — enough to turn "fits in my card" into an eviction at load time.
_registry_sizes: dict[str, float] = {}

#: The registry is a nice-to-have: without it the curated estimates are used.
#: Short timeout because it sits in front of a UI panel, not a download.
_REGISTRY_TIMEOUT = httpx.Timeout(connect=2.0, read=6.0, write=6.0, pool=6.0)
_REGISTRY_ROOT = "https://registry.ollama.ai/v2/library"
_MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"


async def registry_size_gb(client: httpx.AsyncClient, model: str) -> float | None:
    """The real download size of ``model`` in GB, or ``None`` if unknown.

    Sums the manifest's layer sizes — what the pull actually transfers. Any
    failure (offline, proxy, retired tag) answers ``None`` and the caller keeps
    the curated estimate, so an air-gapped machine still sees a usable panel.
    """
    if model in _registry_sizes:
        return _registry_sizes[model]
    name, _, tag = model.partition(":")
    try:
        resp = await client.get(
            f"{_REGISTRY_ROOT}/{name}/manifests/{tag or 'latest'}",
            headers={"Accept": _MANIFEST_ACCEPT},
        )
        if resp.status_code != 200:
            return None
        layers = resp.json().get("layers") or []
        total = sum(int(layer.get("size") or 0) for layer in layers)
    except Exception:  # noqa: BLE001 — an unreachable registry is a normal state
        log.debug("ollama-pull: registry size unavailable for %s", model)
        return None
    if total <= 0:
        return None
    size = round(total / 1e9, 1)
    _registry_sizes[model] = size
    return size


async def _real_sizes() -> dict[str, float]:
    """Registry sizes for the whole shortlist, fetched concurrently."""
    async with httpx.AsyncClient(timeout=_REGISTRY_TIMEOUT) as client:
        results = await asyncio.gather(
            *(registry_size_gb(client, entry.id) for entry in RECOMMENDED_MODELS),
            return_exceptions=True,
        )
    sizes: dict[str, float] = {}
    for entry, result in zip(RECOMMENDED_MODELS, results, strict=True):
        if isinstance(result, float):
            sizes[entry.id] = result
    return sizes


def _pick_recommended(rows: list[dict[str, Any]]) -> None:
    """Mark the best pick per role on ``rows``, in place.

    The best pick is the LARGEST model of that role this machine still runs
    comfortably — bigger is better right up to the point where it stops fitting,
    and that point is the only thing the hardware probe is for. If nothing in a
    role fits comfortably, the SMALLEST one is marked instead: a machine that
    cannot run any of them still deserves a starting point rather than four
    entries all flagged "tight" and no answer to "so which one?".

    A role whose model is already installed marks nothing: the user has made
    that choice, and re-recommending a different size over the top of it is how
    a panel starts nagging.
    """
    for role in ROLE_ORDER:
        in_role = [r for r in rows if r["role"] == role]
        if not in_role or any(r["installed"] for r in in_role):
            continue
        comfortable = [r for r in in_role if r["fit"] == "comfortable"]
        pick = (
            max(comfortable, key=lambda r: r["size_gb"])
            if comfortable
            else min(in_role, key=lambda r: r["size_gb"])
        )
        pick["recommended"] = True


async def recommendations() -> dict[str, Any]:
    """The curated shortlist, ranked for THIS machine.

    Three things happen here that a static list cannot do: sizes come from the
    registry (so the fit verdict is judged against the real download), the
    verdict weighs GPU memory ahead of RAM, and each role gets exactly one
    "recommended" entry — the largest one this box runs well.
    """
    installed, error = await installed_models()
    memory_gb = total_memory_gb()
    accel, accel_source = accelerator_gb()
    real_sizes = await _real_sizes()

    models: list[dict[str, Any]] = []
    for entry in RECOMMENDED_MODELS:
        size = real_sizes.get(entry.id, entry.size_gb)
        verdict, note = fit_verdict(size, memory_gb, accel)
        models.append(
            {
                "id": entry.id,
                "label": entry.label,
                "size_gb": size,
                "purpose": entry.purpose,
                "role": entry.role,
                "tools": entry.tools,
                "vision": entry.vision,
                "installed": _is_installed(entry.id, installed),
                "fit": verdict,
                "fit_note": note,
                "recommended": False,
            }
        )
    _pick_recommended(models)
    # Within a role, largest first: the strongest option a machine can take is
    # the interesting one, and the recommended entry lands at or near the top
    # instead of below three smaller models the user has to scroll past.
    models.sort(key=lambda r: (ROLE_ORDER.index(r["role"]), -r["size_gb"]))

    return {
        "server": server_root(),
        "server_reachable": error is None,
        "message": error or "",
        "memory_gb": memory_gb,
        # The accelerator the verdicts were judged against. 0 with source
        # "none" means "none I could read" — the panel says so rather than
        # implying the machine has no GPU at all.
        "accelerator_gb": round(accel, 1),
        "accelerator_source": accel_source,
        "roles": list(ROLE_ORDER),
        "models": models,
        "installed": sorted(installed),
    }


async def _run_pull(model: str) -> None:
    """Stream ``/api/pull`` into the run state. Never raises."""
    run = _run_for(model)
    root = server_root()
    try:
        async with (
            httpx.AsyncClient(timeout=_PULL_TIMEOUT) as client,
            client.stream(
                "POST", f"{root}/api/pull", json={"model": model, "stream": True}
            ) as resp,
        ):
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
    "ROLE_ORDER",
    "RecommendedModel",
    "Role",
    "accelerator_gb",
    "fit_verdict",
    "installed_models",
    "pull_status",
    "recommendations",
    "registry_size_gb",
    "server_root",
    "start_pull",
    "total_memory_gb",
]
