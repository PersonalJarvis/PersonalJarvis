---
title: "Install Personal Jarvis"
slug: install-personal-jarvis
summary: "Install the app on a supported computer, then confirm that Personal Jarvis starts correctly."
section: "Start here"
section_order: 1
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [installation, setup, windows, macos, linux]
related: [first-run-setup, platform-support, troubleshooting]
---

Personal Jarvis installs on Windows, macOS, and Linux. The standard installation adds the desktop app, prepares supported voice features, and opens Jarvis when it is ready.

You do not choose an artificial intelligence provider or enter a credential in the terminal. That happens later in the app, where Jarvis can store it safely and guide you through the remaining setup.

## Before You Start

You need:

- an internet connection for the app, its dependencies, and voice downloads;
- a regular user account that can approve software installation when asked;
- Python 3.11 through 3.14 and Git, or permission for the installer to add them;
- several minutes for the first installation. Voice downloads can add a few hundred megabytes.

Node.js is optional. Jarvis works without it; Node.js is used only by optional Jarvis-Agent workers and some integrations.

> [!warning] Enter provider credentials only in the app during first-run setup. Never paste a credential into a terminal command, chat, voice request, screenshot, or configuration file.

## Choose an Install Profile

| Profile | What it prepares | What opens afterward | Best for |
|---|---|---|---|
| Full desktop | Desktop app, supported local voice components, channels, and telephony support | The Personal Jarvis app | Most people on a computer with a desktop |
| Headless | The smaller server base without desktop-specific extras | A local web server | A Linux server or computer without a display |

The full profile is the normal choice and does not require a graphics processor. Platform checks skip native components that cannot run on your operating system or processor. Jarvis keeps supported browser, cloud, or simpler local paths available instead of treating one optional component as the whole app.

## Install on Windows

1. Open **PowerShell** from the Start menu. A regular, non-administrator window is enough.
2. Run the official installer:

   ```powershell
   irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
   ```

3. If Python or Git is missing, review the list and approve the offer to install it with WinGet. Windows may show one approval prompt.
4. Keep PowerShell open while the six numbered phases finish. The installer shows a check mark after each completed part and stops with a visible error if a required part fails.
5. Wait for the Personal Jarvis app to open. You can start it again later by searching for **Personal Jarvis** in Windows Search.

## Install on macOS or Linux Desktop

1. Open **Terminal** on macOS, or your preferred terminal on Linux.
2. Run the official installer:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
   ```

3. Choose **Yes** when the terminal asks whether to install Personal Jarvis.

### Finish on macOS

1. If Python or Git is missing, approve the offered Homebrew or system installation. The same installer run checks again and continues when the tools are ready.
2. Wait for the app to open. You can start it again later by searching for **Personal Jarvis** in Spotlight.
3. Use the first-run permission buttons for features you want, such as the microphone or screen access. macOS owns these prompts, and the installer cannot approve them for you. Jarvis remains available for text if you decline a voice or computer-control permission.

### Finish on Linux

1. If Python or Git is missing, approve the supported package-manager step. Your system may ask for administrator approval through `sudo`.
2. Wait for all six phases to finish and for the app to open. On a supported desktop, **Personal Jarvis** is also added to the application menu.

## Install on Headless Linux

Use the headless profile for a Linux server without a graphical desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash -s -- --headless
```

The installer skips desktop extras and the optional Node.js check, then starts the server and prints its local address. On a local machine, open that address in a browser. For a remote server, read [Platform support](platform-support) before making the service reachable; do not expose a local admin address directly to the internet.

## How It Fits Together

1. **The install command starts the process.** Jarvis checks Python and Git, and asks before adding anything that is missing.
2. **The installer fetches and prepares Jarvis.** It creates an isolated Python environment, installs the selected profile, checks the dependencies, and prepares supported voice models.
3. **Your operating system receives the right launcher.** Windows uses Windows Search, macOS uses a stable app identity in Spotlight, and desktop Linux uses the application menu. Headless Linux starts a browser-based server instead.
4. **First-run setup takes over.** The app asks for language, voice choices, permissions, and a provider - the service that answers your requests. Add any required credential in the app, never in the install command.
5. **Chat and voice use the capabilities you enabled.** If a local speech component or preferred provider is unavailable, Jarvis can use another configured provider family or an available browser, cloud, or local path. It tells you when no working option is available.

Read [First-run setup](first-run-setup) for the choices that appear after installation.

## Check That It Works

For a desktop install, look for **Personal Jarvis is ready** in the terminal and confirm that the app opens to the one-time setup guide. Close the app, then start **Personal Jarvis** once from Windows Search, Spotlight, or your Linux application menu.

For a headless install, confirm that the terminal prints a local address and that opening it shows the same setup guide.

## Update Later

Run the same installation command again. A managed installation updates in place, keeps your setup and settings, repairs its launcher when needed, and does not repeat onboarding.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Python 3.11+ not found** or **git not found** | A required tool is missing or too old | Approve the installer offer, or install the named tool from its official source, reopen the terminal, and run the command again. |
| The installer stops during **Dependencies** | A package download, disk write, or required profile component failed | Check the visible error, internet connection, free space, and package-manager status. Then run the same installer again. |
| **Some required voice models are missing** | A model download did not finish | Check the connection and rerun the installer. Available browser or cloud speech paths may still work after setup. |
| **Desktop app registration failed** | Jarvis could not create a reliable operating-system launcher | Do not treat the install as complete. Check operating-system permissions, rerun the installer, then use [Troubleshooting](troubleshooting) if it repeats. |
| The summary appears but no window opens | The app could not launch, or a headless system was detected | Read the final terminal message, try the registered launcher, and confirm your system matches [Platform support](platform-support). |

## Next Steps

- Complete [First-run setup](first-run-setup) to choose your language, permissions, voice options, and provider inside the app.
- Read [Platform support](platform-support) to understand operating-system differences and headless limitations.
- Use [Troubleshooting](troubleshooting) when startup, permissions, audio, or a connected provider does not behave as expected.
