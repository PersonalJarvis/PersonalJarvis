"""Transactional model changes for the managed local realtime server."""

from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger(__name__)

_OLLAMA_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,200}")


class ManagedSetupError(RuntimeError):
    """A setup failed after preserving or restoring the previous stack."""


def build_launch_command(
    current_command: str,
    *,
    brain_model: str,
    voice_model: str,
) -> str:
    """Apply validated model identifiers to a managed launch command."""
    selected_brain = (brain_model or "").strip()
    if not _OLLAMA_TAG.fullmatch(selected_brain):
        raise ValueError("the selected Ollama model name is not valid")
    from jarvis.realtime.local_server import supervisor
    from jarvis.realtime.local_server.model_catalog import apply_voice_profile

    if not supervisor.is_managed_launch_command(current_command):
        raise ValueError("only the Jarvis-managed server can be configured here")
    rewritten = supervisor._replace_brain_model(current_command, selected_brain)
    return apply_voice_profile(rewritten, voice_model)


async def apply_and_test_stack(
    *,
    base_url: str,
    current_command: str,
    brain_model: str,
    voice_model: str,
    language: str,
) -> dict[str, object]:
    """Replace, voice-test, persist, or roll back one complete local stack."""
    from jarvis.realtime.local_server import install, supervisor
    from jarvis.realtime.local_server.smoke import probe_voice_roundtrip

    new_command = build_launch_command(
        current_command,
        brain_model=brain_model,
        voice_model=voice_model,
    )
    outcome = await asyncio.to_thread(
        lambda: supervisor.replace_idle_managed_runtime(
            current_command=current_command,
            launch_command=new_command,
            base_url=base_url,
            reason="model-switch",
        )
    )
    if outcome.startswith("refused:"):
        reason = outcome.partition(":")[2]
        if reason == "call-active":
            raise ManagedSetupError(
                "a voice call is active; finish it before changing local models"
            )
        if reason == "runtime-state-unknown":
            raise ManagedSetupError(
                "the local server is still starting; wait until it is ready and retry"
            )
        raise ManagedSetupError(f"the local server could not switch models ({reason})")

    try:
        ready = await asyncio.to_thread(
            lambda: supervisor.wait_until_ready(
                base_url,
                launch_command=new_command,
                cleanup_on_timeout=True,
            )
        )
        if not ready:
            raise RuntimeError("the selected models did not become ready in time")
        await asyncio.to_thread(lambda: supervisor.warm_brain(launch_command=new_command))
        smoke = await probe_voice_roundtrip(
            base_url,
            language=language,
            timeout_s=90.0,
        )

        await asyncio.to_thread(lambda: install.repair_smoke_marker_from_live_runtime(base_url))
        await asyncio.to_thread(
            lambda: supervisor.start_runtime_monitor(
                launch_command=new_command,
                base_url=base_url,
            )
        )
        runtime = await asyncio.to_thread(supervisor.status, base_url)

        # Persist last: a model is not the active selection until it has
        # produced both a safe transcript and real PCM through the shipped
        # adapter, and its runtime supervision is armed. The config writer
        # supplies lock + atomic replace + BOM handling (AP-7).
        from jarvis.core.config_writer import set_local_realtime_launch_command

        await asyncio.to_thread(set_local_realtime_launch_command, new_command)
        return {
            "ok": True,
            "changed": new_command != current_command,
            "brain_model": brain_model,
            "voice_model": voice_model,
            "smoke": smoke,
            "runtime": runtime,
            "launch_command": new_command,
        }
    except BaseException as exc:
        rollback = await _rollback_stack(
            base_url=base_url,
            failed_command=new_command,
            previous_command=current_command,
        )
        if isinstance(exc, asyncio.CancelledError):
            raise
        message = str(exc) or type(exc).__name__
        if rollback:
            raise ManagedSetupError(
                f"the selected models failed the voice test ({message}); "
                "the previous setup was restored"
            ) from exc
        raise ManagedSetupError(
            f"the selected models failed the voice test ({message}); automatic rollback also failed"
        ) from exc


async def _rollback_stack(
    *,
    base_url: str,
    failed_command: str,
    previous_command: str,
) -> bool:
    """Best-effort rollback that is still fully readiness-verified."""
    from jarvis.realtime.local_server import supervisor

    try:
        outcome = await asyncio.to_thread(
            lambda: supervisor.replace_idle_managed_runtime(
                current_command=failed_command,
                launch_command=previous_command,
                base_url=base_url,
                reason="model-switch-rollback",
            )
        )
        if outcome.startswith("refused:"):
            log.error("local-realtime model rollback was refused: %s", outcome)
            return False
        ready = await asyncio.to_thread(
            lambda: supervisor.wait_until_ready(
                base_url,
                launch_command=previous_command,
                cleanup_on_timeout=True,
            )
        )
        if not ready:
            log.error("local-realtime model rollback did not become ready")
            return False
        await asyncio.to_thread(lambda: supervisor.warm_brain(launch_command=previous_command))
        await asyncio.to_thread(
            lambda: supervisor.start_runtime_monitor(
                launch_command=previous_command,
                base_url=base_url,
            )
        )
        return True
    except Exception:  # noqa: BLE001 - the caller surfaces rollback failure
        log.exception("local-realtime model rollback failed")
        return False
