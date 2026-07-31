"""In-app installation of the optional local speech engines and their models.

CLAUDE.md section 3 is explicit that a capability must be recoverable from
INSIDE the app: entering, switching or repairing a provider may never require
hand-editing ``jarvis.toml`` or dropping to a shell. A local provider makes that
concrete — it needs a pip package and a multi-gigabyte model download before it
can transcribe a word, and telling a non-developer to run pip is exactly the
"works on my machine" failure §3 exists to prevent.

Two halves, tracked separately because they fail separately and are fixed
differently:

* the **engine** (a pip install, needs a wheel for this Python/OS),
* the **model** (a download into the HuggingFace cache or the data dir).

The work runs on a daemon thread and reports progress through a small state
dict the UI polls, because a synchronous route would hold a request open for
minutes and a failed install must stay RETRYABLE rather than raising into a
500. Every terminal state carries an English sentence saying what happened —
never a bare boolean (AP-30).
"""
from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from jarvis.speech.local_models import (
    LocalProvider,
    get_local_provider,
    local_status,
)

log = logging.getLogger("jarvis.speech.local_install")

InstallState = Literal["idle", "running", "done", "error"]


@dataclass
class _InstallRun:
    """Mutable progress of ONE provider's install, guarded by ``lock``."""

    state: InstallState = "idle"
    message: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


#: One run per provider id. Installs for different providers are independent —
#: pulling a TTS voice must not appear to be "already running" because an STT
#: model download is in flight.
_runs: dict[str, _InstallRun] = {}
_runs_guard = threading.Lock()


def _run_for(provider_id: str) -> _InstallRun:
    with _runs_guard:
        return _runs.setdefault(provider_id, _InstallRun())


def _install_engine(entry: LocalProvider) -> tuple[bool, str]:
    """pip-install the inference runtime. Returns (ok, human message)."""
    from jarvis.setup.dependencies import install_pip_package

    # only_binary: this runs on an END USER's machine, where a source build
    # would need a C toolchain (ctranslate2) or FFmpeg headers (av) that are
    # not there. No wheel for this Python/OS must fail FAST with an honest
    # diagnosis rather than after a ten-minute compile that cannot succeed
    # (BUG-059).
    return install_pip_package(entry.pip_package, only_binary=True)


def _download_model(
    entry: LocalProvider, progress: Callable[[str], None] | None = None
) -> None:
    """Fetch the weights for *entry*. Raises with a usable message on failure."""
    if entry.runtime == "faster-whisper":
        # The same cache layout WhisperModel(name) resolves at runtime, so the
        # download the user just paid for is the one the engine finds.
        from jarvis.setup.prefetch import _download_whisper_model

        _download_whisper_model(entry.model_id)
        return
    from jarvis.speech.sherpa_models import download_sherpa_model

    # Every bundle, not just the first: a local voice needs one per language,
    # and stopping after German would leave the assistant mute in English.
    for model_id in entry.bundles:
        download_sherpa_model(model_id, progress=progress)


def _run_install(provider_id: str) -> None:
    """Engine, then model, then publish a terminal state. Never raises."""
    run = _run_for(provider_id)
    entry = get_local_provider(provider_id)
    if entry is None:
        with run.lock:
            run.state = "error"
            run.message = f"'{provider_id}' is not a local provider."
        return

    status = local_status(provider_id)
    ok = True
    message = ""
    if status is not None and not status.engine_installed:
        ok, message = _install_engine(entry)
        # Let THIS interpreter see a package that was not importable when it
        # started; without it the very next readiness probe would still say
        # "not installed" and the UI would offer the install again.
        importlib.invalidate_caches()
    else:
        message = "The local speech engine was already installed."

    if ok:
        try:

            def _note(text: str) -> None:
                """Publish each download step so the UI shows real progress."""
                with run.lock:
                    run.message = text

            _download_model(entry, _note)
            message = f"{entry.model_label} is ready — it runs on this machine now."
        except Exception as exc:  # noqa: BLE001 — surface an honest retry state
            ok = False
            message = f"Could not download {entry.model_label}: {exc}"

    # Trust the probe over the installer's own exit status: a downloader can
    # report success and still leave an unreadable cache, and a card that says
    # "ready" over a broken model is the failure this whole area is about.
    if ok:
        verify = local_status(provider_id)
        if verify is not None and not verify.ready:
            ok = False
            message = (
                f"The install finished, but {entry.model_label} is still not "
                "usable. Try again — the download may have been interrupted."
            )

    with run.lock:
        run.state = "done" if ok else "error"
        run.message = message
    log.info("local install for %s finished: ok=%s msg=%s", provider_id, ok, message[:200])


def start_install(provider_id: str) -> dict[str, Any]:
    """Begin (or join) an install for *provider_id*; returns the current state.

    Idempotent: a second call while a run is in flight reports the running state
    instead of starting a duplicate download.
    """
    entry = get_local_provider(provider_id)
    if entry is None:
        return {
            "state": "error",
            "ready": False,
            "message": f"'{provider_id}' is not a local provider.",
        }
    status = local_status(provider_id)
    if status is not None and status.ready:
        return {
            "state": "done",
            "ready": True,
            "already": True,
            "message": status.detail,
        }
    run = _run_for(provider_id)
    with run.lock:
        if run.state == "running":
            return {"state": "running", "ready": False, "message": run.message}
        run.state = "running"
        run.message = f"Installing {entry.model_label}…"
    threading.Thread(
        target=_run_install,
        args=(provider_id,),
        name=f"local-install-{provider_id}",
        daemon=True,
    ).start()
    return {"state": "running", "ready": False, "message": "Install started."}


def install_status(provider_id: str) -> dict[str, Any]:
    """Current install state plus the independent readiness probe.

    The two can disagree, and when they do the PROBE wins: a user who installed
    the engine some other way (a prior app run, the ``[full]`` extra, a manual
    pip) has a working provider this process never installed, and reporting
    "idle" at them would be false. Likewise a "done" run whose model cannot be
    read afterwards is reported as an error, because it is one.
    """
    entry = get_local_provider(provider_id)
    if entry is None:
        return {
            "state": "error",
            "ready": False,
            "message": f"'{provider_id}' is not a local provider.",
        }
    status = local_status(provider_id)
    ready = bool(status and status.ready)
    run = _run_for(provider_id)
    with run.lock:
        state: InstallState = run.state
        message = run.message
    if ready and state in ("idle", "running"):
        state = "done"
        message = status.detail if status else message
    elif state == "done" and not ready:
        state = "error"
        message = (
            f"The install finished, but {entry.model_label} is not usable yet. "
            "Try again."
        )
    elif state == "idle" and status is not None:
        message = status.detail
    return {
        "state": state,
        "ready": ready,
        "message": message,
        "engine_installed": bool(status and status.engine_installed),
        "model_present": bool(status and status.model_present),
        "model_label": entry.model_label,
        "download_size": entry.download_size,
    }


__all__ = ["install_status", "start_install"]
