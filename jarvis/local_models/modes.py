"""Local model modes: which installed GGUF the managed local brain runs.

A 4 GB GPU holds ONE model at a time (the managed ``llama-server`` runs with
``--models-max 1``, so switching the served model unloads the previous one
before the next loads). The modes map onto the installed models by size, so
no model name is hard-coded and a user who installs different GGUFs gets the
same behaviour:

* ``normal``    — the smallest installed chat model (fast conversation).
* ``developer`` — the largest installed chat model (code work; it may run
  partly on the CPU and is slower, which is the trade the user asked for).

Switching persists ``[brain.providers."local-openai"].model`` (the per-model
picker path, which the brain-provider lock deliberately leaves open), applies
it to the running brain and warms the new model's prompt cache.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.local_models.llama_server import PROVIDER_ID, list_models

log = logging.getLogger(__name__)

MODES: tuple[str, ...] = ("normal", "developer")

#: Held so a fire-and-forget warm-up task is not collected mid-run.
_tasks: set[asyncio.Task[Any]] = set()


def model_for_mode(mode: str) -> str | None:
    """The served model id (file stem) for ``mode``, or None if not installed."""
    models = list_models()  # smallest first
    if not models:
        return None
    if mode == "developer":
        return models[-1].stem if len(models) > 1 else None
    return models[0].stem


def current_mode(configured_model: str | None) -> str:
    models = list_models()
    if len(models) > 1 and configured_model == models[-1].stem:
        return "developer"
    return "normal"


async def switch_mode(mode: str, *, brain: Any = None) -> dict[str, Any]:
    """Persist + live-apply the model for ``mode``. Never raises."""
    if mode not in MODES:
        return {"ok": False, "error": f"unknown mode {mode!r} (use {', '.join(MODES)})"}
    model = model_for_mode(mode)
    if model is None:
        return {
            "ok": False,
            "error": (
                "No second local model is installed for developer mode."
                if mode == "developer"
                else "No local model is installed."
            ),
        }
    try:
        from jarvis.core.config_writer import set_brain_provider_model

        await asyncio.to_thread(set_brain_provider_model, PROVIDER_ID, model=model)
    except Exception as exc:  # noqa: BLE001 - reported to the caller, nothing half-applied
        log.warning("local mode %s: persisting %s failed: %s", mode, model, exc)
        return {"ok": False, "error": f"could not save the model choice: {exc}"}
    if brain is None:
        from jarvis.core import runtime_refs

        brain = runtime_refs.get_brain_manager()
    applied = False
    if brain is not None and hasattr(brain, "apply_provider_model"):
        applied = bool(brain.apply_provider_model(PROVIDER_ID, model))
        prewarm = getattr(brain, "prewarm_prompt_cache", None)
        if applied and callable(prewarm):
            # Loading a model takes seconds to a minute; do it now, off the
            # turn, instead of inside the user's next question.
            task = asyncio.get_running_loop().create_task(prewarm(), name=f"local-mode-{mode}")
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
    log.info("local mode -> %s (%s), applied_live=%s", mode, model, applied)
    return {"ok": True, "mode": mode, "model": model, "applied_live": applied}


__all__ = ["MODES", "current_mode", "model_for_mode", "switch_mode"]
