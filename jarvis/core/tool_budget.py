"""The one time budget every tool in the live voice path is sized against.

A native tool call blocks the live model until its result arrives (ADR-0035
§3), so a slow tool is a mute Jarvis. The maintainer's standing rule
(2026-08-22): whatever a tool does for a spoken request is finished — or has
honestly handed back — within five seconds, for every plugin, not one. Live
2026-08-22 20:01:52 was the counter-example: a ``youtube_music`` play sat 199 s
on a stuck player host while every other tool of the day answered in under 3 s.

Three layers read this number, each from here so they cannot drift apart:

* the realtime session releases the live model with a pending result once a
  native call runs past it (``jarvis/realtime/session.py``);
* tools that wait on something (a player confirming playback, a browser
  registering a media session) size their confirm windows under it, leaving
  room for the request that precedes the wait;
* the REST plugins' shared HTTP pool uses it as the request timeout — a
  single API round trip that needs longer is a hung API, not a slow one, and
  the turn has already been released by then.

A tool that legitimately needs longer (a long job, a download) does not belong
in a synchronous voice call at all: it dispatches and reports back through the
orchestrator, which has its own progress bridge.
"""

from __future__ import annotations

#: Seconds one native tool call may take in the live voice path before the
#: model is released with an honest "still running" result.
VOICE_TOOL_BUDGET_S = 5.0

#: Default request timeout for the REST plugins' shared HTTP pool. Equal to the
#: voice budget on purpose: a round trip that is not back by then cannot make
#: the turn any more, and failing it here frees the connection and logs the
#: cause instead of letting the call ride to the old 20 s transport default.
REST_REQUEST_TIMEOUT_S = VOICE_TOOL_BUDGET_S
