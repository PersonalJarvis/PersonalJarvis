"""Agent chat — the typed front-page chat as an agent session.

The chat surface of the front page used to be a second mouth for the voice
brain: typed text went down the same ``MessageSent`` path the microphone
uses and the router brain answered. Since 2026-08-23 it is its own thing —
a coding-agent session in the Claude Code shape, run on whichever
*agent-tier* provider and model the person picks in the composer, with the
reasoning effort dialled by hand:

* :mod:`.effort`   — which effort levels each provider family offers and how
  a picked level maps onto what a provider actually accepts;
* :mod:`.catalog`  — the provider rows the picker shows (labels, runner
  family, curated model lists for the CLI-backed providers);
* :mod:`.tools`    — the hands of the in-process agent (Read / Write / Edit /
  Ls / Glob / Grep / RunCommand) scoped to the session's working directory;
* :mod:`.events`   — the one event vocabulary both the store and the live
  stream speak;
* :mod:`.store`    — SQLite persistence for sessions and their event log;
* :mod:`.runner_api` / :mod:`.runner_cli` — the two ways a turn runs: the
  provider's own chat API in a tool loop, or a vendor CLI (``claude``,
  ``codex``, ``agy``, ``grok``) driven non-interactively;
* :mod:`.service`  — sessions in flight, subscribers, cancel and approvals.

The voice path is untouched: nothing here subscribes to the event bus, and
nothing on the voice critical path imports this package (AP-9).
"""
