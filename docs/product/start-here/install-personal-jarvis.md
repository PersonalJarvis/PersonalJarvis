---
title: "Install Personal Jarvis"
slug: install-personal-jarvis
summary: "Install Personal Jarvis on a desktop or headless Linux host, choose an update path, and protect your data during repair."
section: "Start here"
section_order: 1
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [installation, setup, updates, windows, macos, linux, headless, pipx]
related: [first-run-setup, local-ai-providers, platform-support, troubleshooting]
---

The recommended one-line installer creates a **managed installation** in its
own Python environment, adds a desktop launcher where supported, and opens the
app. Managed desktop installs can receive published releases inside Jarvis.

No artificial intelligence provider is required for installation. Connect an
online provider or a compatible local model after the app opens.

## Before You Start

You need:

- a supported 64-bit Windows, macOS, or glibc-based Linux computer;
- Python 3.11 through 3.14 and Git;
- an internet connection and writable space for the app and models; and
- permission to approve a package-manager prompt when a tool is missing.

The installer can offer to add Python or Git through a supported system package
manager. The macOS desktop launcher also needs the Xcode Command Line Tools.
When automatic setup is unavailable, the installer shows the manual next step.

Node.js 18 or newer enables some Jarvis-Agent worker tools and integrations but
is optional. A graphics processor is also optional.

> [!warning] Enter provider credentials only in **API Keys & Providers** inside
> the app. Never put a credential in an install command, chat, voice request,
> screenshot, or configuration file.

## Choose an Installation

| Choice | What you get | How updates work | Best for |
|---|---|---|---|
| One-line desktop installer | Full app, local voice support, launcher, and managed environment | In-app published releases | Most users |
| One-line `--headless` installer | Smaller browser-based server | Manual restart and maintenance | Linux without a display |
| PyPI with `pipx` | Isolated base package and `jarvis serve` | `pipx upgrade personal-jarvis` | Existing `pipx` users |
| Manual clone or environment | Source and profile you choose | Your Git and Python workflow | Contributors and custom hosts |

Only the official one-line installer marks its checkout as managed. Package and
manual installs do not show the in-app update button.

## Install with the Recommended Installer

### Windows

1. Open **PowerShell** from the Start menu as a regular user.
2. Run the official installer:

   ```powershell
   irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
   ```

3. Approve any offer to add Python or Git. The installer checks again and
   continues in the same run.
4. Keep PowerShell open until **Personal Jarvis is ready** appears. The app
   opens and becomes available through Windows Search.

### macOS or Linux

1. Open **Terminal**.
2. Choose the desktop command, or the headless command on a Linux server:

   ```bash
   # Desktop on macOS or Linux
   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash

   # Headless Linux
   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash -s -- --headless
   ```

3. Confirm the installation and review any prerequisite or Linux desktop-
   package prompt.
4. Wait for all six phases. Desktop installs open from Spotlight or the Linux
   application menu. Headless installs print a local browser address.

> [!note] These commands download the current public installer. Review the
> [Windows source](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/install/install.ps1)
> or [macOS and Linux source](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/install/install.sh)
> first if you want to inspect it.

## Install the Isolated PyPI Package

If you already have `pipx`, install the smaller browser-based server without a
managed source checkout:

```bash
pipx install personal-jarvis
jarvis serve
```

Open the printed local address. This option has no native desktop launcher or
in-app updater. Upgrade it with `pipx upgrade personal-jarvis`.

## Complete the First Launch

1. Choose the interface and reply language.
2. Review microphone, activation, and operating-system permissions. Text still
   works without microphone or desktop permissions.
3. Finish onboarding, then open **API Keys**. The page title is **API Keys &
   Providers**. Connect only the services or local models you need.
4. On macOS, grant permissions to **Personal Jarvis**, not Terminal or Python,
   so they remain attached to the registered app.

Read [First-run setup](first-run-setup) for every choice or [Local AI
providers](local-ai-providers) to run a compatible model on your computer.

## Update or Repair an Installation

Managed desktop installs show **Update available** for a newer **published
GitHub Release**. The button stages that exact release and restarts Jarvis to
refresh dependencies and desktop files. It never offers unpublished `main`
changes. A failed post-restart installation rolls back and reports the result.

Finish important work first. Restarting interrupts live terminal panes, which
reattach or resume where supported. Jarvis asks before interrupting missions.

Rerun the original one-line command to repair the managed checkout, Python
environment, models, or launcher. It keeps completed onboarding. The command
uses the public `main` installer source, so use the in-app button for normal
release updates.

For a damaged checkout, repair moves the old folder to a dated backup and tries
to restore `data/`, `jarvis.toml`, `.env`, and `wiki/`. It reports failed copies
and leaves the backup in place. Operating-system credential storage is outside
the checkout and is not replaced.

Repair resets tracked application files, so it does **not** preserve arbitrary
source edits. Keep custom code in a manual clone and back up important data and
Wiki content separately.

For other installation types:

- `pipx` follows PyPI through `pipx upgrade personal-jarvis`;
- manual clones follow your Git and Python workflow; and
- headless Linux needs a maintenance window because it cannot complete the
  desktop restart flow.

## How It Fits Together

1. **The installer checks the host.** It finds Python and Git, detects a Linux
   display, and asks before adding missing tools.
2. **The profile prepares the right surface.** Full adds desktop and supported
   local components; headless keeps the smaller server. Incompatible native
   parts are skipped while the rest remains available.
3. **Setup connects features.** Language and activation feed voice; providers
   supply chat, speech, and connected capabilities. Jarvis uses another
   compatible configured path or states what is missing.
4. **The install type controls maintenance.** Managed desktop copies receive
   published releases in-app. PyPI and manual copies use their own workflow.

## Check That It Works

For desktop, confirm **Personal Jarvis is ready**, then close and reopen the app
from Windows Search, Spotlight, or the Linux application menu.

For headless or `pipx`, open the printed local address and confirm the setup
guide loads. Do not expose that administration address directly to the internet.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Python 3.11+ not found** or **git not found** | A tool is missing, old, or not visible | Approve the installer offer or install it officially, then recheck. Use Python 3.11-3.14. |
| Installation stops during **Dependencies** or **Voice models** | A download, disk, package, or model step failed | Read the error, check connection and free space, then rerun the installer. |
| The summary appears but no window opens | Launch was blocked, or Linux is headless | Try the registered launcher; on headless Linux, open the printed address. |
| **Update available** never appears | The install is manual, offline, or already current | Use the package or Git workflow for an unmanaged copy. |
| Repair cannot copy data | A file is locked or unwritable | Keep the dated backup and follow [Troubleshooting](troubleshooting) before copying manually. |

## Next Steps

- Follow [First-run setup](first-run-setup) for language, permissions,
  activation, and providers.
- Read [Local AI providers](local-ai-providers) to run a compatible local model.
- Check [Platform support](platform-support) for desktop and headless differences.
- Use [Troubleshooting](troubleshooting) for deeper help.
