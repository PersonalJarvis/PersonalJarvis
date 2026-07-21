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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [installation, setup, windows, macos, linux]
related: [first-run-setup, platform-support, troubleshooting]
---

Personal Jarvis runs on Windows, macOS, and Linux. On a computer with a desktop, the standard installation adds the full app, installs the supported local voice components, and opens Jarvis when setup is ready. On Linux without a graphical session, the installer uses the smaller headless profile instead.

You do not choose an artificial intelligence provider or enter a credential in the terminal. You do that during first-run setup in the app, where Jarvis stores the credential through its protected credential system.

## Before You Start

Before you run the installer, make sure you have:

- an internet connection for the app, its dependencies, and voice model downloads;
- enough free disk space for the app and its models. Voice model downloads alone can add a few hundred megabytes;
- a regular user account that can approve package-manager or operating-system prompts when needed;
- Python 3.11 through 3.14 and Git. If either is missing, the installer asks before using a supported package manager and otherwise shows the manual installation path.

Node.js 18 or newer is optional. Jarvis works without it. Node.js is used by optional Jarvis-Agent worker command-line tools and some integrations.

> [!warning] Enter provider credentials only in the app during first-run setup. Never paste a credential into a terminal command, chat, voice request, screenshot, or configuration file.

## Choose an Install Profile

| Profile | What it prepares | What opens afterward | Best for |
|---|---|---|---|
| Full desktop | Desktop app, supported local voice components, chat channels, and telephony support | The Personal Jarvis app | Most computers with a desktop |
| Headless | Server base without the full or desktop extras | A local web server | A Linux server or computer without a graphical session |

The installer chooses the full profile on Windows, macOS, and Linux desktops. It automatically chooses the headless profile on Linux when no graphical session is available. The full profile does not require a graphics processor, and dependency markers skip packages that do not support your operating system, processor, or Python version.

## Install on Windows

1. Open **PowerShell** from the Start menu. A regular, non-administrator window is enough.
2. Run the official installer:

   ```powershell
   irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex
   ```

3. If Python or Git is missing, review the list and approve the offer to install the missing tools with WinGet. WinGet or Windows may show more than one approval prompt when both tools are missing.
4. Keep PowerShell open while the six numbered phases run. Status lines show what completed, and the installer stops with a visible error if a required step fails.
5. Wait for the summary to say **Personal Jarvis is ready** and for the app to open. You can start it again later by searching for **Personal Jarvis** in Windows Search.

## Install on macOS or Linux Desktop

1. Open **Terminal** on macOS, or your preferred terminal on Linux.
2. Run the official installer:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
   ```

3. Choose **Yes** when the terminal asks whether to install Personal Jarvis.

### Finish on macOS

1. If Python or Git is missing and Homebrew is available, approve the offered Homebrew installation. If no supported package manager is available, the installer shows the official manual path and waits while you finish it. The same installer run checks again and continues when the tools are ready.
2. Wait for the app to open. You can start it again later by searching for **Personal Jarvis** in Spotlight.
3. Use the first-run permission buttons for features you want, such as microphone or screen access. macOS controls these prompts, so the installer cannot approve them for you. You can continue with text if you skip voice or computer-control permissions.

### Finish on Linux

1. If Python or Git is missing, approve the supported package-manager step. Your system may ask for administrator approval through `sudo`.
2. On a graphical Linux session, the installer may also offer optional desktop tools for window control and the app window. Declining them does not stop the installation, but some desktop actions may be unavailable.
3. Wait for all six phases to finish and for the app to open. **Personal Jarvis** is also added to the application menu.

## Install on Headless Linux

Use the headless profile for a Linux server without a graphical desktop:

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash -s -- --headless
```

The headless profile skips the full desktop extras and desktop registration. Node.js is not required. When installation finishes, Jarvis starts the server and prints its local address. Open that address in a browser on the same computer. For a remote server, read [Platform support](platform-support) before making the service reachable. Do not expose a local admin address directly to the internet.

## How It Fits Together

1. **The install command checks the computer.** On macOS and Linux, you first confirm that you want to install Jarvis. All platforms check Python and Git, then ask before installing a missing prerequisite.
2. **The installer downloads Jarvis and creates a dedicated Python environment.** It installs the selected profile, checks dependency consistency, downloads voice models, and reports anything that is still missing. A required dependency failure stops the installation. A model download problem is reported so you can retry it.
3. **The desktop profile registers a launcher.** Windows adds Personal Jarvis to Windows Search and Installed Apps. macOS creates an app for Spotlight. Desktop Linux adds an application-menu entry. Headless Linux starts a local web server instead.
4. **First-run setup takes over.** The app guides you through language, provider credentials, permissions, and a wake word or keyboard shortcut. Enter credentials only in the app.
5. **Jarvis checks capabilities when you use chat or voice.** Unsupported native components are skipped during installation. At runtime, Jarvis uses an available configured provider or local path and tells you when none is ready.

Read [First-run setup](first-run-setup) for the choices that appear after installation.

## Check That It Works

For a desktop install, look for **Personal Jarvis is ready** in the terminal and confirm that the app opens to the one-time setup guide. Close the app, then start **Personal Jarvis** from Windows Search, Spotlight, or the Linux application menu.

For a headless install, confirm that the terminal prints a local address and that opening it shows the same setup guide.

## Update Later

Run the same installation command again. The quick installer updates a managed installation to the current `main` branch, keeps your setup and settings, repairs the launcher when needed, and does not repeat onboarding.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Python 3.11+ not found** or **git not found** | A required tool is missing, too old, or not visible in the current terminal | Approve the installer offer, or install the named tool from its official source. Use Python 3.11 through 3.14. Then let the installer check again or rerun it. |
| The installer stops during **Dependencies** | A package download, disk write, or required profile component failed | Read the visible error. Check the internet connection, free space, supported Python version, and package-manager status before running the installer again. |
| **Some required voice models are missing** | A model download or verification did not finish | Check the connection and rerun the installer. Browser or cloud speech can still work after you configure a compatible provider. |
| **Desktop app registration failed** | Jarvis could not create the operating-system launcher it needs | Check the diagnostic path printed by the installer, review operating-system permissions, and rerun the installer. Use [Troubleshooting](troubleshooting) if it fails again. |
| The summary appears but no window opens | The app did not launch, or the installer detected a headless Linux session | Read the final terminal message, try the registered launcher, and confirm that your system matches [Platform support](platform-support). |

## Next Steps

- Complete [First-run setup](first-run-setup) to choose your language, provider credentials, permissions, and activation method inside the app.
- Read [Platform support](platform-support) to understand operating-system differences and headless limitations.
- Use [Troubleshooting](troubleshooting) when startup, permissions, audio, or a connected provider does not behave as expected.
