# Security policy

Personal Jarvis works close to untrusted content. It reads web pages, documents and
screenshots, it listens to a microphone, and with the desktop extras it drives the mouse and
keyboard. That shapes how it is built, and it shapes what we consider a security bug.

## Reporting a vulnerability

Report security issues privately. Please do not open a public issue.

The preferred route is GitHub's private vulnerability reporting: the "Report a vulnerability"
button on this repository's Security tab. It opens an advisory only the maintainers can see.
If that does not work for you, ask a maintainer on [Discord](https://discord.gg/x7USduHxbc)
for a private channel before you share any details.

Include what you found, which component it affects, and how to reproduce it. You will get an
acknowledgement as soon as we reasonably can, and updates while it is being fixed. Please
give us a chance to ship that fix before going public.

## Supported versions

Releases are cut from `main`, and security fixes land there. There are no long-term support
branches, so please test against the latest `main`.

## How it defends itself

These properties come from the architecture, so they hold no matter which providers you
configure.

**Instructions come only from you.** What Jarvis reads through a tool, a web page, an email,
a document, a screenshot, a file, is data and never a command. When observed content contains
text aimed at the agent, it gets surfaced to you instead of executed.

**Every tool call is classified before it runs.** The four tiers are `safe`, `monitor`, `ask`
and `block`, and a blacklist entry always outranks a whitelist entry. `ToolExecutor.execute()`
is the only authorized execution path; calling a tool directly is a bug, not a shortcut.

**Secrets stay out of reach.** They are read only through `get_secret()`, which goes to the OS
credential manager first, then the environment, then a `.env` fallback for development. They
never live in code, in config, or in a commit. The voice and chat paths refuse to accept a
secret at all, because a spoken API key would end up in a speech-to-text log.

**Configuration changes can be undone.** A self-modification runs through validate, back up,
swap atomically, reload synchronously, roll back on failure, and write an audit entry.
Generated skills are created as drafts and are never activated on their own.

**Workers are isolated.** Background work runs in a fresh `git worktree` with kill-on-crash
containment, and never writes into your working tree.

**Elevation is per action.** Administrative steps are requested one at a time over a signed
IPC channel and audited. Nothing runs globally elevated.

## Privacy

Your conversations, the Knowledge Wiki, your contacts and your configuration are files on
your own machine. Nothing is sent anywhere except to the providers and integrations you
configured yourself, and speech recognition can run entirely locally if you would rather no
audio leaves the machine at all. There is no analytics and no tracking in the product.

On the repository side, a public git history is permanent, so the protection is preventive
rather than corrective. Personal data directories, `.env` files, the local config and all key
material are gitignored and never tracked. Credentials are blocked at commit time by hooks
that scan for key material, and GitHub's secret scanning with push protection stays on as the
backstop. A blocked push is treated as a real finding to fix, never as something to work
around. If you still find personal data or a credential in the repository or its history,
report it privately as described above. It is handled as a security issue.

## Scope

In scope: the `jarvis` package, the desktop app, the plugin system, the tool-use and
risk-tier path, the self-modification pipeline, and mission and worker isolation.

Out of scope: vulnerabilities in third-party provider APIs, which belong to that provider;
problems caused by your own misconfiguration; and vulnerabilities in upstream dependencies,
which belong upstream. We track those advisories through Dependabot and update as fixes land.

## What you are actually running

Personal Jarvis is a capable local agent. With the optional desktop extras it can see your
screen, type, run shell commands and place phone calls. Treat the API keys you give it and
the permissions you grant it the way you treat your own credentials. The defaults are
conservative, but review what you switch on before you point it at production systems or at
data you cannot afford to leak.
