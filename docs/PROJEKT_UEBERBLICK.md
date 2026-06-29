# Personal Jarvis
## Whitepaper — Vision, System, Product

**As of:** April 2026
**Author:** Personal Jarvis Project
**Category:** Personal AI orchestrator for the desktop

---

## Executive Summary

Personal Jarvis is not a voice assistant. It is a **personal meta-orchestrator** for the Windows desktop — a system that does at the desk what a human assistant would do: listen, understand, delegate, verify, report. Behind the voice surface there is not a single language model, but a conducted ensemble. Various AI systems, tools, skills, and specialized Jarvis-Agents work together over a shared, interchangeable architecture. The ambition: a digital assistant that does not ask about every little thing, that is not chained to a single provider, that distinguishes between a trivial and a critical task, that can verify its own answers — and that does all of this while sensitive data can stay local. This whitepaper describes the vision behind it, the system, what it can do today, and where the journey is headed.

---

## 1. The Problem

Voice assistants have been on the market for over a decade. Yet hardly anyone uses them for more than timers, music control, and weather queries. That is no coincidence — the reasons are structural:

**They are voice-first, not task-first.** The entire technology stack is built around speech recognition, not around the work the user wants done. As soon as the task becomes more complex than "play song X", the architecture breaks down.

**They are monolithic.** One provider, one model, one ecosystem. If that model is weak at a task, there is no way out. If it goes down, the assistant is mute. If the provider raises prices or discontinues features, the user has no bargaining power.

**They are annoying.** "Are you sure you want to...?" on every other command trains the user to avoid the assistant. After three weeks the device ends up in a drawer.

**They have no memory.** Every command stands on its own. There is no reference to the earlier session, no understanding of what the user is currently seeing on screen, no thread across the hours of a workday.

**They can't really "do" anything.** They can speak — but they can't work with the computer. No browser, no files, no code, no real action on the system.

Personal Jarvis was created as an answer to exactly these five problems.

## 2. The Idea

Instead of building a voice assistant that uses AI models, Personal Jarvis flips the script. The system is designed as an **orchestrator** that treats AI models like tools in a workshop — and voice is just one of several possible inputs.

Concretely, this means five design decisions that set the system apart from everything that has existed so far in this market segment.

**First: multiple brains, used context-dependently.** Jarvis talks to nine different language models — Anthropic's Claude in several variants, GPT, Gemini, Grok, OpenRouter as an aggregator, and two Ollama paths for local models. Which model is responsible for a given request is decided by a fast router based on the task class: fast and cheap for smalltalk, thorough for complex reasoning tasks, code-specific when programming.

**Second: specialized Jarvis-Agents for complex tasks.** When the main Jarvis recognizes that a request cannot be handled with a single tool call — for example "build me a web scraper for this website and test it on three examples" — it delegates to a Jarvis-Agent. That is a separate, more capable model instance that may work autonomously in an isolated workspace for up to thirty minutes before reporting back. The main Jarvis meanwhile stays responsive for other requests.

**Third: interchangeable tools instead of hard wiring.** Jarvis can control the browser, write and execute code, issue shell commands, capture and analyze screenshots, launch applications and interact with them. All of these capabilities are implemented as interchangeable so-called "harnesses". Today Jarvis-Agents is the default code worker; tomorrow it could be a different tool. The higher-level logic notices nothing of it.

**Fourth: skills instead of hard wiring.** Custom workflows are stored as simple Markdown files with a configuration header. They can be triggered by voice commands, system events, or schedules. The user can extend the system without touching a line of code — a growing library of personal routines that over time reflect one's own working style.

**Fifth: voice is only the frontend.** The same orchestration logic should later also be reachable from Telegram, WhatsApp, or a web interface. The interfaces are cut such that connecting a new input channel is a plugin, not a refactoring.

## 3. How the System Is Built

Jarvis is structured as an eight-layer system. At the very top is the user interface — the floating Orb display at the edge of the screen and the desktop app with chat history, skill overview, and live status. Below it sits the orchestrator, the actual brain of the operation: it decides which model, which tool, which Jarvis-Agent is responsible for a task. One layer deeper are the tool adapters, then the language models themselves, then the risk and security assessment, then the speech processing — wake word, speech recognition, speech output — and finally the audio and hardware layers.

The rule between these layers is strict: each layer may talk to the next only over clearly defined interfaces, never laterally. This discipline has two consequences that are noticeable in everyday use.

First, true **interchangeability** emerges. When a new language model comes to market, a new plugin is written and registered — the rest of the system notices nothing. The same holds for speech-output providers as well as for tools. This interchangeability is not just technical elegance, but a strategic hedge against vendor lock-in.

Second, **traceability** emerges. Every action runs over a central event bus with immutable, typed messages. The system practically writes its own history. On errors, the exact sequence can be reconstructed — who said what to whom and when, which model answered what, which tool executed which command. This is the foundation for debugging, auditing, and for the future training of one's own models on one's own interaction data.

## 4. What Jarvis Can Do Today

The system has grown across five completed development phases and is in productive use.

The **speech pipeline** runs bilingually and automatically detects whether the user is currently speaking German or English — no hard language pinning, no switching commands needed. Wake-word detection runs permanently in the background, but is trigger-based: only when "Jarvis" is uttered do the more expensive components wake up. Speech recognition itself runs locally on the GPU with Whisper-based models — that is fast and works without a cloud round-trip.

The **desktop app** is a modern FastAPI-backend / web-frontend combination, rendered in a native window. Here the user sees chat history, active skills, MCP server connections, the model routing, and live streams of running Jarvis-Agent missions. Alongside it floats the Orb display — an animated sphere with soft waves that pulses depending on system state: listening, thinking, speaking, busy.

The **skill system** lets the user create custom routines as Markdown files. A skill is at its core a YAML header — name, trigger, required tools, risk assessment — followed by an instruction in plain text. The trigger can be a speech pattern ("open my standup"), a system event ("when a new image lands in the Downloads folder"), or a schedule ("every Monday at nine"). The library grows with usage.

The **tool system** provides the system with a growing collection of actions. Browser control runs over a logged-in Chrome — Jarvis sees the same web pages the user sees, with all logins. Code generation and execution runs over Jarvis-Agents in an isolated worktree. Shell commands, app launches, file operations, screenshots, screen analysis — all of that is reachable over the same interface.

The **risk-tier system** classifies every tool into one of four levels: safe, monitor, ask, blocked. A whitelist lets the user exempt frequently used actions from the confirmation requirement. Whoever trusts the browser tool once does not want to be asked on every click — and will not be. That is the system's anti-confirmation-fatigue policy.

The **output filter** protects the speech output. Before Jarvis speaks, every model output runs through a rule-based scrubber that removes tool JSON, stack traces, leftover Markdown, engineering jargon, and unwanted forms of address. The filter works exclusively with patterns, never with a second LLM call — latency is sacred when the user is waiting for an answer.

The **multi-provider fallback** ensures robustness. When the primary provider comes back with a rate limit, the system automatically switches to the next one in the chain — without the user noticing. A tracker prevents retry storms in doing so. In the extreme case a local model steps in, so that the system also works offline, albeit with reduced feature scope.

The **model-container architecture (MCP)**, finally, allows two things: Jarvis can use external MCP servers as tools — and it can itself provide an MCP server, so that other agents and tools can access Jarvis. This makes the system a hub in the growing ecosystem of personal AI agents.

## 5. Self-Control and Security

A system that independently issues commands, writes code, and navigates the browser needs built-in self-control. Personal Jarvis has three mechanisms for it.

The **risk-tier mechanism** decides before each action whether it passes through without a query, whether it is logged, whether it must be confirmed, or whether it is blocked entirely. These levels are not rigid — the user can specify via configuration exactly which patterns they consider safe. The system does not thereby learn on its own, but it adapts.

The **worker-critic mechanism** — currently in the final phase of implementation — is the more ambitious safety net. When a Jarvis-Agent completes a task, a second model instance independently checks whether the result is correct. On deficiencies there is a concrete correction instruction — up to three times the worker can improve. This reflection loop catches the typical hallucination and omission errors that a single model would not recognize on its own. Important: the speech output to the user is never voted on by the critic loop itself — it reads out exclusively runtime-signed observations. This prevents a brained model from getting stuck in an endless loop of self-confirmation.

The **audit trail**, finally, logs every action on the system. When Phase 7 is introduced tomorrow — self-modification, i.e. that Jarvis may change its own configuration — that happens exclusively over a hard allowlist and with full backup rollback. Security-relevant areas, API keys, permissions are explicitly not self-modifiable.

## 6. Privacy and Hybrid Architecture

Personal Jarvis is designed as a **hybrid system**. Speech recognition runs locally — the audio data does not leave the device. Speech output and most model calls run in the cloud, because the quality there is significantly higher. This default state is a pragmatic trade-off between data protection and capability.

For more sensitive use cases a **privacy profile** is available that keeps all processing local — at the cost of speed and model quality. That is explicitly intended as an option the user consciously activates, when for example processing confidential information.

Secrets — API keys, tokens, credentials — are stored exclusively in the Windows Credential Manager. They do not appear in configuration files, not in logs, not in speech transcripts. The setup wizard is the only place where the user enters them. Voice and chat fundamentally do not accept secrets — the user cannot speak them in, because that would make speech-recognition logs a leak vector.

## 7. Where We Want to Go

Three major development strands shape the near future of the project.

**Self-healing and self-modification.** The next phase gradually makes Jarvis a co-developer of itself. Voice-driven configuration changes — "Wechsel die Stimme auf die andere" (switch the voice to the other one), "Stell mein primäres Modell auf Gemini" (set my primary model to Gemini) — are enabled over a controlled path: allowlist, validation, backup, rollback on malfunction. Skill authoring by voice command — the user describes what a new skill should do, a Jarvis-Agent writes it as a draft, the user activates it after review. The system does not thereby become autocratic, but it becomes a tool that builds itself further.

**Awareness layer — continuous context.** Today Jarvis knows nothing of the last hour. The awareness layer will change that: a four-stage memory system that holds seconds in working memory, minutes in a rolling session chain, days in a searchable index, and long-term insights in a curated file. At the wake word the main Jarvis knows the thread of the workday — without the critical speech path becoming slower for it. The hard rule: awareness code never runs in the voice path.

**More input channels.** The plugin layer for input channels has only the web input wired up today. Telegram and WhatsApp are the next targets — Jarvis as a personal assistant that is reachable even when the user is not sitting at the desk. A short voice message in the chat triggers the same orchestrator that otherwise runs on the laptop.

In the medium term the desktop frontend should move to a more modern render layer — from the current pywebview solution to a Tauri-based setup with better performance and a smaller footprint. In the long term we are thinking about a marketplace connection for skills and MCP servers, so that the user community can share routines, similar to how it works in the VS Code or Obsidian ecosystem.

## 8. Why This Is Different

Three points distinguish Personal Jarvis from what else exists on the market.

**It is a tool, not a service.** The system runs on the user's machine. No central cloud infrastructure, no provider account, no monthly subscription for usage. If the user wishes, they can run the entire system locally.

**It is provider-agnostic.** Multi-provider is not a feature, but an architectural premise. It is not possible to integrate a single AI provider so deeply that the system no longer functions without it. This discipline is firmly anchored in the code.

**It is tailored to the personal work context.** The system lives on a Windows desktop, knows the setup, the applications, the keyboard shortcuts, the installed tools. It is not an attempt to build a universal cloud assistant — it is the deliberate focus on what a single person at a single computer productively needs.

## Conclusion

Personal Jarvis is an attempt to close the gap between the large language models and the actual work at the computer. Not through a new model, but through an architecture that conducts existing models such that they work together like tools in a well-sorted workshop. The measure of success is not demo-readiness, but everyday usefulness over weeks and months. The system is in productive use today, grows continuously, and the next development horizon lies in giving the assistant a real memory and the ability to self-evolve.

In the end Jarvis should do what a human assistant would do. No more, but also no less.
