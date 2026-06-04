"""Channel adapter layer (OpenClaw-inspired, plan §17.3 / phase 1a).

A channel is a bidirectional transport that delivers user messages to the
orchestrator and returns replies along with event mirrors. The web UI is
the first channel — Telegram/WhatsApp follow in phase 8+. A channel is
**not** the same as a brain provider or a tool: it sits at L5.5 between
the UI (L7) and the orchestrator (L6).

The structural :class:`ChannelAdapter` protocol lives in
:mod:`jarvis.core.protocols`; the concrete dataclasses ``ChannelMessage``
and ``ChannelSession`` are defined and re-exported here.
"""
from __future__ import annotations

from jarvis.core.protocols import ChannelAdapter

from .base import ChannelMessage, ChannelSession

__all__ = ["ChannelAdapter", "ChannelMessage", "ChannelSession"]
