# Security Policy

Personal Jarvis runs workloads that sit close to untrusted content — voice, web pages,
documents, and computer-use loops — so security is built into the architecture, not bolted
on. This document explains how to report a vulnerability and how the system defends itself.

## Reporting a vulnerability

**Please report security issues privately — do not open a public issue.**

- **Preferred:** use GitHub's private vulnerability reporting — the **"Report a vulnerability"**
  button on this repository's **Security** tab. It opens a private advisory only the
  maintainers can see.
- **Alternative:** reach out on [Discord](https://discord.gg/UPu6pFWrJ) and ask a maintainer
  for a private channel before sharing any details.

Please include a clear description, the affected component, and reproduction steps. We'll
acknowledge your report as soon as we reasonably can and keep you updated on the fix. Please
give us a chance to ship a fix before any public disclosure.

## Supported versions

Personal Jarvis ships from `main`; security fixes land there. There are no long-term-support
branches — please test against the latest `main`.

## Security model

Security is enforced by the architecture, so the same guarantees hold regardless of which
providers you configure:

- **Instruction-source boundary.** Only the user — via the chat or voice interface — issues
  instructions. Everything observed *through tools* (web pages, emails, documents, screenshots,
  file contents) is treated as **data, not commands**. Instructions embedded in observed
  content are surfaced to the user, never executed.
- **Risk-tier policy.** Every tool call is classified `safe` / `monitor` / `ask` / `block`
  with the precedence *blacklist > whitelist > tool default*. `ToolExecutor.execute()` is the
  **only** authorized execution path — direct `Tool.execute()` calls are forbidden.
- **Prompt-injection defenses.** The lean Router-Brain dispatches to isolated workers, and a
  regex-only output filter scrubs the spoken path. Observed content cannot escalate privileges
  or redirect data to endpoints it suggests.
- **Secret handling.** Secrets are accessed only through `get_secret()` (OS credential manager
  → environment → `.env` dev fallback). They never live in code, config, or commits, and the
  voice/chat path never accepts secrets (an STT-log-leak vector).
- **Self-modification safety.** Configuration changes go through a 10-step
  *validate → backup → atomic-swap → synchronous reload → rollback → audit* pipeline, and any
  generated skills are created as `draft` and never auto-activated.
- **Worker isolation.** Background workers run in fresh, isolated `git worktree` branches with
  kill-on-crash containment, and never write to the user's working tree.
- **Least-privilege elevation.** Administrative actions are requested per-action and audited
  over a signed (HMAC) IPC channel — never globally elevated.

## Scope

**In scope:** the `jarvis` package, the desktop app, the plugin system, the tool-use and
risk-tier path, the self-modification pipeline, and the mission/worker isolation.

**Out of scope:** vulnerabilities in third-party provider APIs (report to the provider);
issues caused by user misconfiguration; and vulnerabilities in upstream dependencies (report
upstream — we track advisories via Dependabot and update as fixes land).

## A note on the threat model

Personal Jarvis is a powerful local agent: with the optional desktop extras it can see your
screen, type, run shell commands, and place calls. Treat the API keys you configure and the
permissions you grant it the way you would treat your own credentials. The defaults are
conservative; review what you enable before pointing it at production systems or sensitive data.
