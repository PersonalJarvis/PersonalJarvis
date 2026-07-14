"""Completeness self-check ("doctor") — honestly report what is registered and
ready vs. what is advertised but missing.

Motivation (forensic 2026-06-28): a phantom ``openclaw`` harness (advertised in a
tool description + an ``[harness.openclaw].enabled = true`` config block, but never
registered as an entry-point) made Jarvis claim a sub-agent feature was "not
available / not installed" — even though the real sub-agent path (``spawn_worker``
→ Mission-Manager) worked the whole time. The lesson: a fresh download can *look*
complete while a single dead reference makes a working feature appear missing.

This module generalises the defence: it cross-checks every advertised capability
against what is actually registered/installed, and reports honestly:

  * **router tools** — every name in ``ROUTER_TOOLS`` must resolve to a registered
    entry-point (or a known non-entry-point tool). A name that resolves to neither
    is a PHANTOM (the dispatch-to-harness/openclaw class of bug).
  * **harness config** — any harness enabled in ``jarvis.toml`` must be a
    registered harness; an enabled-but-unregistered harness is inert dead config.
  * **sub-agent backend** — the sub-agent code ships in the base install, but the
    *worker* needs an external CLI (``claude`` / ``codex`` / ``agy``). That CLI is a
    separate prerequisite — the real "downloaded but a feature won't run" trap.
  * **brain provider** — Jarvis needs a configured primary brain provider to think.

The result is a flat list of :class:`DoctorFinding`. ``run_doctor`` never raises:
each check is isolated so one failure cannot blind the rest. The CLI wrapper
(``python -m jarvis --doctor``) renders it and exits non-zero on any ``fail``.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Literal

log = logging.getLogger(__name__)

Status = Literal["ok", "warn", "fail", "info"]

# Sub-agent worker CLIs, in the order Jarvis would prefer them. Each is an
# external binary the base ``pip install`` does NOT ship — the sub-agent code is
# in the package, but the worker that does the heavy lifting is one of these.
_WORKER_CLIS: tuple[tuple[str, str], ...] = (
    ("claude", "npm i -g @anthropic-ai/claude-code  (Claude Max OAuth — the default worker)"),
    ("codex", "npm i -g @openai/codex  (ChatGPT login)"),
    ("agy", "the Antigravity / Gemini CLI (Google login)"),
)


@dataclass(frozen=True)
class DoctorFinding:
    """One self-check result.

    Attributes:
        category: Short group key ("router-tools", "harness-config",
            "subagent-backend", "brain-provider").
        status: "ok" (green), "warn" (works but degraded / dead config),
            "fail" (broken — something advertised cannot work), "info" (neutral
            fact, never affects the exit code).
        message: One-line human-readable summary.
        hint: Optional actionable next step shown under the message.
    """

    category: str
    status: Status
    message: str
    hint: str | None = None


def _resolve_cli(name: str) -> str | None:
    """Return the on-PATH binary for ``name``, honouring Windows shims.

    Mirrors ``jarvis.setup.dependencies._resolve_binary`` (npm globals resolve to
    ``.cmd`` on Windows) without importing a private symbol.
    """
    direct = shutil.which(name)
    if direct:
        return direct
    for ext in (".cmd", ".exe", ".bat", ".ps1"):
        hit = shutil.which(name + ext)
        if hit:
            return hit
    return None


def check_router_tools() -> list[DoctorFinding]:
    """Every ``ROUTER_TOOLS`` name must resolve to a registered backend.

    Resolvable = a ``jarvis.tool`` entry-point (incl. the virtual loaders
    ``cli-tools`` / ``plugin-tools`` / ``mcp-tools`` which ARE entry-points) OR a
    known non-entry-point router tool (the Phase-7 self-mod tools, registered
    directly in the loader). A name that resolves to NEITHER is a phantom: the
    brain is told the tool exists, but it can never load — the exact shape of the
    dispatch-to-harness/openclaw bug.
    """
    try:
        from jarvis.brain.factory import (
            ROUTER_TOOLS,
            SELF_MOD_TOOL_NAMES_ROUTER,
        )
    except Exception as exc:  # noqa: BLE001
        return [DoctorFinding("router-tools", "fail",
                              f"could not import ROUTER_TOOLS: {exc}")]
    try:
        tool_eps = {ep.name for ep in entry_points(group="jarvis.tool")}
    except Exception as exc:  # noqa: BLE001
        return [DoctorFinding("router-tools", "fail",
                              f"tool entry-points unreadable: {exc}")]

    known = tool_eps | set(SELF_MOD_TOOL_NAMES_ROUTER)
    phantom = sorted(t for t in ROUTER_TOOLS if t not in known)
    if phantom:
        return [DoctorFinding(
            "router-tools", "fail",
            f"{len(phantom)} router tool(s) advertised but not registered: "
            + ", ".join(phantom),
            hint=("Remove them from ROUTER_TOOLS, or register a jarvis.tool "
                  "entry-point. This is the phantom-tool class of bug — the brain "
                  "is told the tool exists but it can never load."),
        )]
    return [DoctorFinding(
        "router-tools", "ok",
        f"all {len(ROUTER_TOOLS)} router tools resolve to a registered backend",
    )]


def check_harness_config(config: Any) -> list[DoctorFinding]:
    """Any harness enabled in config must actually be a registered harness.

    Generalises the ``[harness.openclaw].enabled = true`` case: an enabled but
    unregistered harness is inert dead config that can mislead routing into
    requesting a vehicle that can never run.
    """
    findings: list[DoctorFinding] = []
    try:
        from jarvis.harness.manager import HarnessManager
        registered = set(HarnessManager().available())
    except Exception as exc:  # noqa: BLE001
        return [DoctorFinding("harness-config", "fail",
                              f"harness registry unreadable: {exc}")]

    harness_config = getattr(config, "harness", None)
    configured_enabled = {
        str(name)
        for name in (getattr(harness_config, "enabled", ()) or ())
    }
    inert_enabled = sorted(configured_enabled - registered)
    if inert_enabled:
        findings.append(DoctorFinding(
            "harness-config", "warn",
            "configured harnesses are not registered: " + ", ".join(inert_enabled),
            hint=("Remove the stale names from [harness].enabled. Heavy work "
                  "uses spawn_worker; MCP integrations expose capability-gated "
                  "tools instead of harness names."),
        ))

    worker_harness = getattr(harness_config, "jarvis_agent", None)
    if worker_harness is None:
        worker_harness = getattr(harness_config, "openclaw", None)
    worker_registered = bool({"jarvis_agent", "openclaw"} & registered)
    if (
        worker_harness is not None
        and getattr(worker_harness, "enabled", False)
        and not worker_registered
    ):
        findings.append(DoctorFinding(
            "harness-config", "warn",
            "[harness.jarvis_agent].enabled = true, but no Jarvis-Agent "
            "worker harness is registered - the block is inert",
            hint=("Set [harness.jarvis_agent].enabled = false. Heavy Jarvis-Agent "
                  "work runs through spawn_worker regardless."),
        ))

    findings.append(DoctorFinding(
        "harness-config", "info",
        "registered harnesses: " + (", ".join(sorted(registered)) or "(none)"),
    ))
    return findings


def check_subagent_backend() -> list[DoctorFinding]:
    """The sub-agent path needs an external worker CLI (claude / codex / agy).

    The sub-agent *code* ships in the base install, but the worker that runs the
    heavy task is one of these external binaries — a separate prerequisite the
    fresh download does not bundle. If NONE is present, spawned sub-agents fail
    with an unactionable "binary not found"; this surfaces it honestly up front.
    """
    available: list[str] = []
    for name, _hint in _WORKER_CLIS:
        if _resolve_cli(name) is not None:
            available.append(name)

    if available:
        return [DoctorFinding(
            "subagent-backend", "ok",
            "sub-agent worker CLI available: " + ", ".join(available),
        )]
    return [DoctorFinding(
        "subagent-backend", "warn",
        "no sub-agent worker CLI found (claude / codex / agy) — spawned "
        "sub-agents cannot run until one is installed and logged in",
        hint="Install the default worker: " + _WORKER_CLIS[0][1],
    )]


def check_brain_provider(config: Any) -> list[DoctorFinding]:
    """Jarvis needs a configured primary brain provider, or it cannot think."""
    try:
        primary = getattr(getattr(config, "brain", None), "primary", None)
    except Exception as exc:  # noqa: BLE001
        return [DoctorFinding("brain-provider", "fail",
                              f"brain config unreadable: {exc}")]
    if not primary:
        return [DoctorFinding(
            "brain-provider", "fail",
            "no primary brain provider configured ([brain].primary is empty)",
            hint="Run `python -m jarvis --wizard` to select and key a provider.",
        )]
    return [DoctorFinding(
        "brain-provider", "info",
        f"primary brain provider: {primary}",
    )]


def run_doctor(config: Any) -> list[DoctorFinding]:
    """Run every completeness check and return a flat, ordered finding list.

    Never raises: each check is isolated so a single failing probe cannot blind
    the rest of the report.
    """
    findings: list[DoctorFinding] = []
    checks: tuple[tuple[str, Any], ...] = (
        ("router-tools", lambda: check_router_tools()),
        ("harness-config", lambda: check_harness_config(config)),
        ("subagent-backend", lambda: check_subagent_backend()),
        ("brain-provider", lambda: check_brain_provider(config)),
    )
    for category, fn in checks:
        try:
            findings.extend(fn())
        except Exception as exc:  # noqa: BLE001 — a probe crash must not blind the doctor
            log.debug("doctor check %s crashed: %s", category, exc)
            findings.append(DoctorFinding(
                category, "fail", f"check crashed: {type(exc).__name__}: {exc}",
            ))
    return findings


def has_failures(findings: list[DoctorFinding]) -> bool:
    """True when any finding is a hard ``fail`` (drives the CLI exit code)."""
    return any(f.status == "fail" for f in findings)
