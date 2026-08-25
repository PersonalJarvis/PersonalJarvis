"""Agent chat — the typed chat: Jarvis with a keyboard, and the IDE's coding sessions.

One package, two surfaces (``store.SURFACES``):

* ``"jarvis"`` — the front page's chat. What you type is input to Jarvis:
  the same assistant the microphone talks to, on the model the composer
  picked for THIS chat (:mod:`.runner_brain` — ``BrainManager.generate``
  with a per-turn override; the live voice brain is never switched). A
  subscription CLI seat (Claude Code, Codex, …) runs as Jarvis through
  :mod:`.runner_cli` + :mod:`.jarvis_harness`, so the subscription pays.
  The folder chip is Jarvis' working folder (:mod:`.folder_tools`), and the
  one permission ladder — Ask / Auto-accept edits / Bypass, plus Plan —
  decides what asks through a clickable card (:mod:`.approval_bridge`).
* ``"agent"`` — a plain coding-agent session in a folder, the Claude Code
  shape, which the Agentic IDE's chat mode runs: a vendor CLI or the API tool
  loop, with the vendor's own permission ladder.

The pieces:

* :mod:`.effort`   — which effort levels each provider family offers and how
  a picked level maps onto what a provider actually accepts;
* :mod:`.catalog`  — the provider rows the picker shows (labels, runner
  family, curated model lists for the CLI-backed providers);
* :mod:`.tools`    — the hands of the in-process agent (Read / Write / Edit /
  Ls / Glob / Grep / RunCommand) scoped to the session's working directory;
* :mod:`.events`   — the one event vocabulary both the store and the live
  stream speak;
* :mod:`.store`    — SQLite persistence for sessions and their event log;
* :mod:`.runner_api` / :mod:`.runner_cli` / :mod:`.runner_brain` — the
  three ways a turn runs: the provider's own chat API in a coding-agent tool
  loop, a vendor CLI (``claude``, ``codex``, ``agy``, ``grok``) driven
  non-interactively, or Jarvis' own brain;
* :mod:`.folder_tools` / :mod:`.approval_bridge` — the Jarvis surface's
  hands in the folder and the card that answers the executor's ticket;
* :mod:`.service`  — sessions in flight, subscribers, cancel and approvals.

The voice path is untouched: nothing here subscribes to the event bus, and
nothing on the voice critical path imports this package (AP-9).
"""
