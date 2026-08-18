"""Who — if anyone — can answer the approval gate for one tool call.

An ``ask``-tier tool must not run until somebody authorizes it. WHO that
somebody is depends entirely on the surface the call arrives from, and the
surfaces differ in kind:

``conversational``
    A human sits inside the turn (voice, realtime, chat). They cannot click a
    modal mid-sentence, so the executor defers the action and the surface ASKS
    on the next turn (``VOICE_CONFIRM_SENTINEL`` → the user's "ja" resumes it
    via ``ToolExecutor.execute_confirmed``).

``interactive``
    A human can answer OUT OF BAND while the call waits. Today that is exactly
    one channel: the mission deck's tool-approval panel
    (``MissionToolApprovalCoordinator`` behind
    ``POST /missions/{id}/tool-approvals/{trace_id}/approve``). Blocking for
    the approval timeout is correct here — the answer really can arrive.

``unattended``
    Nobody is reachable at all: a scheduled workflow, a cron run, a one-shot
    CLI invocation, a background job. Waiting cannot produce an answer, so the
    executor must not pretend it might. Pre-authorization bridges
    (``TaskAutoApprover``, ``MissionToolAutoApprover``) get their chance —
    they answer synchronously while ``ActionApprovalRequired`` is being
    published — and if none of them speaks up the call fails FAST and is
    reported as "approval was impossible", never as "the user refused".

Why this split exists (audit GT-12): every surface except voice and realtime
used to fall into the blocking wait. With no approval UI outside missions, a
CLI, REST, workflow, or chat call to an ask-tier tool sat silent for the full
60-second timeout and then recorded that timeout as a DENIAL — a minute of
nothing, followed by the wrong reason.

**The surface is declared by the calling LAYER, never by the model.** A tool
call's ``args`` come from the LLM; ``config_snapshot`` is built in code by the
surface itself (``jarvis/brain/tool_use_loop.py``,
``BrainSupervisorToolGateway``, ``WorkerToolBroker``). ``resolve_approval_
surface`` reads the snapshot ONLY — an ``args`` key of the same name is
invisible to it — so no prompt and no tool argument can talk the executor into
a channel that asks less. Unknown values are ignored rather than trusted, and
the fallback chain never widens what may run: it only decides who gets asked.

This module holds no policy about WHICH tools need approval. The tier
precedence (blacklist > whitelist > tool default) lives in
``jarvis.safety.risk_tier`` and is untouched by anything here.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final, Literal, cast

log = logging.getLogger(__name__)

ApprovalSurface = Literal["conversational", "interactive", "unattended"]

#: A human is inside the turn — defer and ask on the next one.
CONVERSATIONAL: Final[ApprovalSurface] = "conversational"
#: A human can decide out of band while the call waits (mission deck).
INTERACTIVE: Final[ApprovalSurface] = "interactive"
#: Nobody can decide — fail fast and say so.
UNATTENDED: Final[ApprovalSurface] = "unattended"

VALID_APPROVAL_SURFACES: Final[frozenset[str]] = frozenset(
    {CONVERSATIONAL, INTERACTIVE, UNATTENDED}
)

#: ``config_snapshot`` key a surface uses to declare itself explicitly.
APPROVAL_SURFACE_KEY: Final[str] = "approval_surface"

#: The original boolean, kept as a first-class alias for ``conversational``.
#: Voice and realtime have set it since the two-turn confirmation flow shipped
#: and their behaviour must stay byte-identical.
VOICE_CONFIRM_KEY: Final[str] = "voice_confirm"

#: Presence of a mission id means the mission deck can render and answer this
#: call. It is stamped by ``BrainSupervisorToolGateway`` from the broker's
#: ``SupervisorToolRequest``, i.e. in code, out of the model's reach.
MISSION_ID_KEY: Final[str] = "mission_id"


def resolve_approval_surface(
    config_snapshot: Mapping[str, Any] | None,
) -> ApprovalSurface:
    """Decide which approval channel this call actually has.

    Order (first match wins):

    1. An explicit, valid ``approval_surface`` from the calling layer.
    2. ``voice_confirm=True`` — the legacy conversational declaration.
    3. A non-empty ``mission_id`` — the mission deck can answer out of band.
    4. Otherwise ``unattended``: no channel is known to exist, so the honest
       answer is that nobody can approve.

    An unrecognised ``approval_surface`` string is logged and ignored; the
    remaining rules then apply. That is deliberate — a typo must not silently
    grant a call a channel it does not have, and it must not crash the gate
    either.
    """
    snapshot: Mapping[str, Any] = config_snapshot or {}

    declared = snapshot.get(APPROVAL_SURFACE_KEY)
    if declared is not None:
        text = str(declared).strip().lower()
        if text in VALID_APPROVAL_SURFACES:
            return cast(ApprovalSurface, text)
        log.warning(
            "Unknown %s=%r — falling back to the derived surface",
            APPROVAL_SURFACE_KEY,
            declared,
        )

    if bool(snapshot.get(VOICE_CONFIRM_KEY)):
        return CONVERSATIONAL

    if str(snapshot.get(MISSION_ID_KEY) or "").strip():
        return INTERACTIVE

    return UNATTENDED


__all__ = [
    "APPROVAL_SURFACE_KEY",
    "CONVERSATIONAL",
    "INTERACTIVE",
    "MISSION_ID_KEY",
    "UNATTENDED",
    "VALID_APPROVAL_SURFACES",
    "VOICE_CONFIRM_KEY",
    "ApprovalSurface",
    "resolve_approval_surface",
]
