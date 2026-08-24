"""The chat surface — Jarvis with a keyboard.

**This is not the Agentic IDE's chat.** The two look alike on purpose and mean
different things; keeping them apart is the whole point of this package's
existence, and mixing them up has cost two rebuilds already:

* **The chat section** (this package, ``components/home/ChatStage``) is JARVIS.
  You type instead of speaking, and the same assistant answers — the same
  brain, tools, memory, wiki and confirmations the microphone reaches. It has
  no working directory, writes no code on its own, and its history is your
  conversations with Jarvis.
* **The Agentic IDE's chat** (``jarvis/agentic_ide``,
  ``components/agentic/IdeChatSurface``) is a CODING AGENT. It reads and drives
  the terminal sessions running in the workspace folder you opened — one pane,
  its conversation rendered as an exchange. Its subject is code and files.

Same shape, different job: one is who you talk to, the other is what you have
working for you.

What runs a turn here, and who pays for it:

* :mod:`.runner_brain` — an API-key row. Jarvis' own brain thinks with that
  provider's model (``BrainManager.generate``), billed per token. The turn's
  steps are mirrored into the timeline from the app's event bus, so you see
  the tools it reached for and the sentence it wrote next to each one.
* :mod:`.runner_cli` — a subscription row (Claude Max, ChatGPT, Antigravity,
  Grok). The vendor CLI runs the loop — that is what the seat pays for — but
  inside the Jarvis harness (:mod:`.jarvis_harness`): Jarvis' own tools over
  MCP and a preamble telling it whose hands those are, so it acts as Jarvis
  rather than as a coding assistant that happens to sit in this folder.

The rest of the package:

* :mod:`.catalog`  — the provider rows the picker shows, and which runner each
  one uses;
* :mod:`.effort`   — the reasoning-effort ladders, per provider and per model;
* :mod:`.permissions` — the vendor permission ladders (CLI rows only: the
  brain has none, Jarvis' own risk tiers gate its tools);
* :mod:`.events`   — the one event vocabulary the store and the live stream
  both speak;
* :mod:`.store`    — SQLite persistence for sessions and their event log;
* :mod:`.tools`    — the in-process file/shell tools of the API runner;
* :mod:`.service`  — sessions in flight, subscribers, cancel and approvals.

The voice path is untouched: nothing here sits on it, and the step mirror is a
read-only bus subscriber that never reaches the publisher (AP-9, AP-18).
"""
