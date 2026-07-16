---
title: "Platform Support and Requirements"
slug: platform-support
summary: "Check operating-system support, optional hardware and audio capabilities, headless behavior, and graceful feature fallbacks."
section: "Reference"
section_order: 7
order: 6
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [platforms, windows, macos, linux, headless, requirements, compatibility]
related: [install-personal-jarvis, permissions, configuration-reference, troubleshooting]
---

Personal Jarvis supports Windows, macOS, Linux desktops, and headless Linux.
The shared app, browser interface, chats, settings, tasks, and Jarvis-Agent
views are portable. Features that touch the physical computer are more
specific: a microphone, global shortcut, floating Bar, screen capture, or
mouse action can work only when the host exposes the matching capability.

For that reason, **supported** does not mean that every feature behaves
identically on every host. Jarvis checks the current operating system,
display session, installed components, and permissions, then enables the best
available implementation. An unavailable optional capability should leave the
rest of the app usable and explain what is missing.

## Baseline Requirements

| Requirement | Current support | Why it matters |
|---|---|---|
| Operating system | Windows, macOS, or Linux | The installer and runtime select native behavior for these three families |
| Python | 3.11 through 3.14 | The package requires Python 3.11 or newer and does not yet accept Python 3.15 |
| Git | Any recent release | The installer fetches Jarvis, and Jarvis-Agent missions use isolated Git worktrees |
| Internet connection | Required for installation; normally required for online providers and downloads | An already prepared local feature can keep working offline, but online models and services cannot |
| Writable user storage | Required | Jarvis needs space for its environment, settings, models, logs, and user data |

You also need at least one configured brain provider or supported subscription
login for AI replies. This is an app connection, not an installation
prerequisite. Add it inside **API Keys & Providers** after installation.

The installer can offer to add missing Python or Git with the host's normal
package manager. On macOS and Linux, it prefers a Python version with broad
native speech-package coverage. The project itself accepts Python 3.14, but
some optional local voice components do not yet publish packages for every
Python, operating-system, and processor combination.

## Install Profiles

| Profile | Included surface | Intended host |
|---|---|---|
| **Full** | Desktop app, platform-specific desktop components, supported local voice components, telephony support, and chat-channel support | A Windows, macOS, or Linux computer with a graphical desktop |
| **Headless** | The smaller base application, local server, browser interface, API, and WebSocket service | A Linux server, container, or computer without a display |

The supported one-line installer chooses the full profile on a normal desktop.
On Linux, it chooses the headless profile when neither an X11 nor Wayland
display is present. Passing `--headless` selects that profile explicitly.

The full profile uses platform markers. It installs the components that can
run on the current operating system and skips incompatible native packages.
This keeps one installer usable across platforms, but it also means that a
successful full install does not promise every optional speech engine on every
processor. The installer stops if the required profile or desktop registration
fails instead of presenting a partial desktop install as ready.

The headless profile deliberately omits desktop extras. It does not create an
application-menu entry, local overlay, global desktop shortcut, or physical
mouse and keyboard controller. It starts the browser-based server unless
launching was disabled.

## Feature Support Matrix

| Feature | Windows desktop | macOS desktop | Linux desktop | Headless Linux |
|---|---|---|---|---|
| Web app, chat, settings, Docs, tasks, and outputs | Supported | Supported | Supported | Supported in a browser |
| Registered desktop app | Supported | Supported app bundle | Supported application-menu entry | Not available |
| Local microphone and speakers | Supported when devices are available | Permission-dependent | Requires working PortAudio and device access | Not part of the headless surface |
| Browser voice | Supported by a compatible browser | Supported by a compatible browser | Supported by a compatible browser | Supported; remote microphone use requires HTTPS |
| Wake word and local speech engines | Capability-dependent | Capability-dependent | Capability-dependent | No local microphone; use a browser or another configured channel |
| Global voice shortcuts | Supported | Permission-dependent | X11 only when a compatible backend is present; unavailable on Wayland | Not available |
| Jarvis Bar and mascot | Bar and mascot supported | Bar supported; mascot unavailable | Best effort on a compatible graphical session | No on-screen surface |
| Computer Use | Supported on a real desktop | Permission-dependent | X11 only and capability-dependent | Not available |

**Capability-dependent** means Jarvis has an implementation and checks the
host before using it. A missing dependency, device, display, or permission can
still make that feature unavailable on one otherwise supported computer.

## Voice, Audio, and Wake Differences

Local desktop voice needs a usable microphone, speaker, and audio backend.
Windows and macOS normally provide the operating-system layer. Linux also
needs the PortAudio system library, commonly provided by a package named
`libportaudio2`, plus permission to access the selected device. Use
**Settings > Audio devices > Rescan** after adding or reconnecting hardware.

A graphics processor is optional. The normal profiles do not require PyTorch
or a GPU, and supported local voice paths can run on the CPU. A compatible GPU
can accelerate eligible offline speech work; it is not a requirement for
chat, browser voice, or cloud speech.

Some alternate offline speech packages have narrower native support. In
particular, Windows on ARM and some Python 3.14 combinations cannot install
every WebRTC or local Whisper component. Jarvis keeps simpler local detection,
browser voice, or a configured online speech provider available where the
matching path exists. It should report that an optional engine is unavailable
rather than fail the whole application.

Browser voice is separate from the host's local audio pipeline. A browser on
the same computer can use a localhost address. When the browser connects to a
remote headless server, microphone capture requires a secure HTTPS context;
plain remote HTTP can still display the text interface, but browsers normally
block microphone access there.

## Desktop and Computer Use Differences

Personal Jarvis takes one capability snapshot when it starts. The snapshot
checks for a display, a global-hotkey backend, a terminal backend, a desktop
accessibility tree, an overlay, a readable cursor, and an elevation mechanism.
Individual probes fail closed to **unavailable** instead of stopping startup.

Windows uses native UI Automation for named interface elements and native
input for mouse and keyboard actions. macOS uses the Accessibility and screen
capture interfaces and therefore needs the matching permissions for Computer
Use. Linux can use AT-SPI for named interface elements when the distribution's
accessibility packages and desktop bus are available.

If a native interface tree is missing, Computer Use can fall back to
screenshots and pixel positions. That fallback is less descriptive: Jarvis may
see a button but not know its accessible name. It does not overcome the two
hard boundaries below:

- **Wayland:** global shortcuts, global cursor access, window control, and
  synthetic mouse or keyboard input are restricted. Computer Use refuses the
  action honestly; running one app through XWayland does not provide a complete
  control backend.
- **Headless:** without a real display there is nothing to capture or control.
  Text chat, server APIs, browser views, missions, and file work can still run.

On macOS, grant permissions to the installed **Personal Jarvis** app, not to a
terminal or Python interpreter. The installed app bundle provides the stable
identity to which macOS attaches the grant. The Jarvis Bar runs through a
desktop companion; the mascot surface is intentionally unavailable there.

Linux overlays are best effort because display servers and compositors differ.
On X11 with a display and Tk support, Jarvis can attempt the on-screen surface.
Otherwise it uses a tray-level or no-visible-surface fallback, while chat and
voice continue independently.

## Optional Components

| Component | Required? | What it adds |
|---|---|---|
| Provider credential or subscription login | Required for AI replies, not for installation | Chat and the provider-backed features assigned to that connection |
| Node.js 18 or newer | No | Optional Jarvis-Agent worker command-line tools and some Node-based integrations |
| GPU | No | Faster eligible offline speech processing |
| Linux PortAudio library | Only for local Linux audio | Physical microphone and speaker access through the desktop pipeline |
| Linux AT-SPI packages and desktop bus | Only for native Linux UI labels | Named interface elements for more reliable Computer Use on X11 |
| macOS privacy grants | Only for the feature named by each grant | Microphone, global shortcuts, screen capture, accessibility, and input control |
| Graphical display | Only for desktop surfaces | Desktop window, overlays, screen capture, and physical Computer Use |

## Graceful Fallbacks

| Preferred capability is missing | Expected result |
|---|---|
| Desktop display | Run the headless server and use the browser interface |
| Local audio device or backend | Keep text chat; use browser voice or another configured voice channel where available |
| One native local speech engine | Use another compatible local, browser, or configured online speech path |
| Global shortcut | Start voice from the app, or use the wake word when local microphone capture works |
| Native accessibility tree | Use screenshot and pixel-based Computer Use when screen capture and input are otherwise supported |
| Overlay or tray surface | Continue without an on-screen voice surface |
| Preferred provider | Cross to another configured provider family when the feature supports it, or show an honest unavailable state |

Fallback does not mean silent success. A missing capability should produce an
unavailable status, log message, or refusal that names the boundary. It should
not claim that a screen action, permission, or connection worked when it did
not.

## Verification Status

Implementation, automated tests, and a live desktop observation prove
different things. The repository's recorded sign-off includes a Windows
desktop verification dated 2026-05-30 and a headless Linux base-install and
import verification in a `python:3.11-slim` container dated 2026-06-20.

The same record does not yet contain a dated live macOS desktop or Linux GUI
sign-off for global shortcut capture, native accessibility trees, overlays, or
interactive elevation. Those paths are implemented and capability-gated, but
should be treated as host-dependent until tested on the specific desktop in
front of you. This limitation does not apply to the already verified headless
Linux base path.

## How It Fits Together

1. **The installer chooses a profile.** A desktop gets the full profile;
   headless Linux gets the server base unless you override the choice.
2. **Platform markers select installable native components.** Incompatible
   optional packages are skipped without changing the shared app.
3. **Startup probes the current host.** Jarvis records what this session can
   actually provide, including the display, audio-adjacent desktop features,
   input backends, and accessibility interfaces.
4. **Permissions and configuration narrow the result.** An installed feature
   still waits for the required operating-system grant, device selection, and
   provider connection.
5. **Each feature selects an implementation or fallback.** Chat can remain
   healthy while an overlay, shortcut, local speech engine, or screen action is
   unavailable.

This is why platform support connects directly to installation, permissions,
and configuration. The operating system defines what is possible, permissions
define what is allowed, and your settings choose which available path Jarvis
uses.

## Check That It Works

With Personal Jarvis running, check the shared service first:

```text
jarvis --json system status
```

A successful response confirms that the installed command can reach the local
Jarvis service. It does not prove every optional desktop capability.

On a desktop, open **Settings > Audio devices** and select **Rescan**. Seeing
the intended input and output confirms the local audio path; an explicit
unavailable state is the correct result on a headless host. Then test only the
feature you plan to use: the wake-word test for wake, the permissions card on
macOS, or a small reversible Computer Use action on a real X11, macOS, or
Windows desktop.

For headless Linux, open the local address printed by the installer and confirm
that the browser interface loads. Use text first. If the browser is on another
computer, configure secure remote access before testing its microphone.

## Troubleshooting

| What you see | Likely boundary | What to do |
|---|---|---|
| Installation rejects Python | Python is older than 3.11 or is 3.15 or newer | Use Python 3.11 through 3.14, or let the supported installer choose a compatible version |
| Linux starts a server instead of a desktop window | No X11 or Wayland display was detected | Use the printed browser address, or start the installer from the intended graphical session |
| Linux microphone or speakers are unavailable | PortAudio, device access, or the selected device is missing | Install the distribution's PortAudio library, reconnect the device, and use **Rescan** |
| A macOS shortcut or screen action does nothing | The installed app lacks the matching privacy grant or needs a restart | Review **Settings > Privacy permissions**, grant only the needed access, and restart when asked |
| Computer Use refuses on Linux | The session is Wayland or has no display | Use an X11 desktop for Computer Use; keep using text, browser, and server features on the current host |
| Named Linux interface elements are missing | AT-SPI or its desktop bus is unavailable | Install the distribution's accessibility packages and use an X11 desktop session; screenshot fallback may still work |
| One local voice engine is unavailable after a successful install | No compatible native package exists for this Python, processor, or operating system | Use an available local, browser, or configured online speech path; do not replace unrelated provider credentials |
| The Bar, mascot, or tray is absent | The selected surface is unsupported, the session has no compatible display, or the compositor rejected it | Keep using the app; choose a supported appearance and restart only if the setting asks |

## Next Steps

- Follow [Install Personal Jarvis](install-personal-jarvis) for the supported
  full and headless installation paths.
- Review [App Permissions](permissions) before enabling microphone, shortcut,
  screen, accessibility, or input access.
- Use the [Configuration Reference](configuration-reference) to choose a
  feature path on managed or headless installations.
- Continue with [Troubleshooting](troubleshooting) when a capability that
  should be available on your host still fails its focused check.
