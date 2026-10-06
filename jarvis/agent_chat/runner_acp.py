"""Plan a society turn on an external agent runtime (Hermes, OpenClaw).

The CLI runner (``runner_cli``) owns the process for every CLI seat — spawn,
containment, cancellation, rollover, resume-lost retries, approvals — and
these runtimes are just one more seat to it. This module answers the part
that differs: which agent the chat belongs to, which model route and Jarvis
tools that agent gets, and which process to start (from the runtime's
driver in ``jarvis.agent_runtimes``). The turn itself is driven over ACP by
``jarvis.agent_runtimes.acp.AcpTurn``, handed to the runner on the plan.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from jarvis.agent_runtimes import RUNNER_RUNTIMES, driver
from jarvis.agent_runtimes.acp import AcpTurn
from jarvis.agent_runtimes.base import RuntimeTurn, RuntimeUnavailable
from jarvis.agent_runtimes.model_map import RouteUnavailable, route_for

log = logging.getLogger(__name__)

#: Society capability ids whose explicit denial switches the runtime's own
#: matching tools off as well.
_NATIVE_GROUPS: dict[str, str] = {"core:shell": "shell", "core:browser": "web"}


def supports_runner(runner: str) -> bool:
    return runner in RUNNER_RUNTIMES


def _agent_id(session_id: str) -> str:
    parts = session_id.split(":", 2)
    return parts[1] if len(parts) > 1 and parts[0] == "society" else ""


async def _agent(agent_id: str) -> Any:
    from jarvis.society.runtime import current_runtime

    runtime = current_runtime()
    if runtime is None or not agent_id:
        return None
    return await runtime.roster.get(agent_id)


def _config() -> Any:
    from jarvis.society.runtime import current_runtime

    runtime = current_runtime()
    cfg = runtime.config() if runtime is not None else None
    if cfg is not None:
        return cfg
    from jarvis.core.config import load_config

    return load_config()


def _denied_native(agent: Any, plan_mode: bool) -> frozenset[str]:
    denied = {group for cap, group in _NATIVE_GROUPS.items() if cap in (agent.denies or [])}
    if plan_mode:
        denied.add("shell")
    return frozenset(denied)


async def plan_runtime_turn(
    handle: Any,
    runner: str,
    *,
    prompt: str,
    cwd: Path,
    resume: str | None,
    identity: Any | None,
) -> Any:
    """The ``CliPlan`` for one turn on ``runner``; raises ``CliUnavailable``."""
    from jarvis import __version__
    from jarvis.agent_chat import jarvis_harness
    from jarvis.agent_chat.runner_cli import (
        _PLAN_PREAMBLE,
        CliPlan,
        CliUnavailable,
        _with_identity,
    )

    session = handle.session
    runtime_name = RUNNER_RUNTIMES[runner]
    agent_id = _agent_id(session.session_id)
    agent = await _agent(agent_id)
    if agent is None:
        raise CliUnavailable(f"{runtime_name.title()} runs society agents only.")
    try:
        route = await asyncio.to_thread(route_for, _config(), session.provider, session.model)
    except RouteUnavailable as exc:
        raise CliUnavailable(str(exc)) from exc
    plan_mode = session.permission_mode in ("plan", "read-only")
    tools = not getattr(handle, "tools_disabled", False)
    turn = RuntimeTurn(
        agent_id=agent.agent_id,
        agent_name=agent.name,
        session_id=session.session_id,
        workspace=cwd,
        route=route,
        resume=resume,
        auto_approve=session.permission_mode == "bypass",
        mcp_url=jarvis_harness.endpoint() if tools else None,
        control_key=jarvis_harness.control_key() if tools else None,
        denied_native=_denied_native(agent, plan_mode),
    )
    runtime = driver(runtime_name)
    try:
        launch = await runtime.launch(turn)
    except RuntimeUnavailable as exc:
        raise CliUnavailable(str(exc)) from exc
    text = _PLAN_PREAMBLE + prompt if plan_mode else prompt
    acp = AcpTurn(
        turn_id=handle.turn_id,
        cwd=str(launch.cwd),
        prompt_text=_with_identity(text, identity, resume),
        resume=launch.acp_resume,
        mcp_servers=launch.mcp_servers,
        auto_allow=turn.auto_approve,
        auto_deny=plan_mode,
        client_version=__version__,
        report_session=launch.vendor_session,
    )
    return CliPlan(
        argv=launch.argv,
        env=launch.env,
        stdin_text=None,
        shape="acp",
        vendor_session=None,
        keep_stdin=True,
        acp=acp,
        after_turn=launch.release,
    )
