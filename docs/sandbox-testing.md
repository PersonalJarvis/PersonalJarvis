# Isolated Jarvis Sandbox — testing the public build safely

A throwaway, fully isolated copy of Jarvis, provisioned from a fresh clone of the
public GitHub repo, so you experience **exactly what a stranger downloading it
gets** — without touching your real config, data, or saved API keys.

Design: [`docs/superpowers/specs/2026-06-24-jarvis-sandbox-testing-design.md`](superpowers/specs/2026-06-24-jarvis-sandbox-testing-design.md).

## Why you can't just run a second copy

A second native Jarvis started from a clone would otherwise share three
machine-scoped things with your real install and quietly collide with it:

- the **Windows Credential Manager** namespace (`personal-jarvis`) — it would read
  your real API keys (spending your billing) and could overwrite them;
- the **editable-install pin** — `pip install -e .` re-points the global
  `import jarvis` to the clone;
- your real **config and data** on disk.

The sandbox seals all of these.

## The four seams it seals

| Seam | How it's isolated |
|---|---|
| Python import | a dedicated venv inside the sandbox; the global `import jarvis` is untouched |
| Credentials | `keyring` is redirected to an isolated throwaway file backend — your real Credential Manager is never read or written |
| Config | `JARVIS_CONFIG` points at the sandbox's own `jarvis.toml` |
| Data | the separate clone makes the repo-relative `data/` resolve inside the sandbox; `LOCALAPPDATA` is also redirected so the subsystems that use the OS user-data dir (board stats, user skills, contacts, cli config — `%LOCALAPPDATA%\Jarvis`) stay in the sandbox too, instead of the real shared location |

**Computer-Use is forced OFF** in the sandbox — a single physical machine cannot
sandbox global mouse/keyboard/screenshot actions. The sandbox talks and shows its
UI; it never drives your desktop. **Voice stays ON.**

## Use it

```powershell
# 1. Provision from GitHub (fresh clone, isolated venv, isolation proofs):
pwsh scripts\sandbox\new-jarvis-sandbox.ps1

# 2. CLOSE your real Jarvis first — voice shares the one mic/speakers.

# 3. (optional) put one provider key in the sandbox's .env, then launch:
pwsh ..\Jarvis-Sandbox\run-sandbox.ps1

# 4. When done, tear it down (goes to the Recycle Bin, reversible):
pwsh scripts\sandbox\remove-jarvis-sandbox.ps1
```

The sandbox runs on its own port (default 47830), so it never clashes with your
real app on 47821. The prebuilt web UI ships inside the repo now, so the sandbox
needs no Node build.

## Useful flags

| Flag | Effect |
|---|---|
| `-SandboxRoot <path>` | where to build it (default: a `Jarvis-Sandbox` sibling of the repo) |
| `-Ref <branch\|tag>` | which published version to clone (default: `main`) |
| `-Port <int>` | sandbox web/admin port (default: 47830) |
| `-Force` | replace an existing sandbox (old one goes to the Recycle Bin) |
| `-Launch` | provision **and** launch in one go (otherwise it prints the launch command) |

## What "isolated" is proven by

The provisioner prints PASS/FAIL for each before it ever launches: the sandbox's
`import jarvis` resolves inside the sandbox; the global `import jarvis` is
unchanged from the baseline; the keyring backend is the file store (not
`WinVaultKeyring`); and the prebuilt UI is present.
