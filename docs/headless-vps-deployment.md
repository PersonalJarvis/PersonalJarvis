# Running Jarvis headless on a VPS

A plain Ubuntu box, no screen, no microphone, no GPU. You talk to it from your
laptop's browser; the server does the thinking.

Everything here works today — this guide only collects it in one place. The
part worth reading even if you skip the rest is **[What degrades, and what goes
quiet](#5-what-degrades-and-what-goes-quiet)**: two capabilities turn themselves
off on a headless host, and if you do not know that, the install reads as
broken.

---

## What you get, and what you do not

| Works on a headless VPS | Does not |
|---|---|
| Chat, missions, wiki, tasks, the whole web UI | The desktop window, the Orb overlay, the tray |
| Voice, using **your laptop's** microphone through the browser | The server's own microphone (there isn't one) |
| Cloud speech-to-text and text-to-speech | Local wake word and local speech-to-text |
| The `jarvis` control CLI, locally and over SSH | Screen control / computer use (nothing to look at) |

---

## 1. Install

The base profile is cloud-first: no torch, no GPU, no Node.js, no audio
hardware assumed.

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash -s -- --headless --no-launch
```

`--headless` skips the desktop-automation tools and the Node.js check.
`--no-launch` installs without starting anything, which is what you want before
a service exists to own the process.

Prefer to do it by hand, or already have a Python environment:

```bash
python3 -m venv ~/jarvis-venv
~/jarvis-venv/bin/pip install personal-jarvis
```

You need Python 3.11 or newer. Nothing else — the base install pulls no system
audio libraries, so a container image works too.

> **A note on `libportaudio`.** The install guide mentions it for Linux. That is
> for a machine with a real microphone. On a VPS you do not need it: the audio
> comes from your laptop's browser, not from the server's sound card.

---

## 2. Set the keys — one is enough

Jarvis is built so that any single provider key gets you a working system. You
do not need one of each.

The setup normally happens in the app's UI, which you do not have yet, so use
the environment instead. Every key follows the same pattern:

```bash
# Pick ONE of these; the rest are optional.
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export OPENROUTER_API_KEY=...
```

With one key, Jarvis wires the whole chain from it — the brain, and cloud
speech-to-text and text-to-speech where that provider offers them. Where it
does not, the tier degrades honestly and says so, rather than pretending.

Keys can also live in the OS credential manager. On a headless Linux box that
usually means the Secret Service, which needs a session bus that a VPS often
does not have — so the environment is the practical answer here, and the
service unit below is where it belongs.

> **Never put a key in `jarvis.toml`.** It is a config file, not a vault, and
> it is the file most likely to end up in a backup or a paste.

---

## 3. Start it, and keep it running

By hand, to see that it works:

```bash
~/jarvis-venv/bin/jarvis serve
```

That starts the API, the WebSocket and the browser UI on port **47821** —
loopback only. Confirm from the same box:

```bash
curl -s localhost:47821/api/health
```

### The systemd unit

Save as `/etc/systemd/system/jarvis.service` (adjust the user and paths):

```ini
[Unit]
Description=Personal Jarvis (headless)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=jarvis
WorkingDirectory=/home/jarvis
# One key is enough. Keep this file mode 600 — it holds a credential.
Environment=OPENAI_API_KEY=...
ExecStart=/home/jarvis/jarvis-venv/bin/jarvis serve
Restart=on-failure
RestartSec=5
# Journald gets the logs; `journalctl -u jarvis -f` follows them.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo chmod 600 /etc/systemd/system/jarvis.service   # it holds a key
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis
journalctl -u jarvis -f
```

A **system** unit, not a `--user` one: a user unit stops when the login session
ends, which is the opposite of what a server wants. (The `--user` autostart path
in the codebase is for a Linux *desktop*, where Jarvis has to be inside the
graphical session to reach a microphone.)

### Reaching it from your laptop

The safe answer is an SSH tunnel. It needs no firewall change, no certificate,
and no exposed port:

```bash
ssh -L 47821:127.0.0.1:47821 you@your-vps
```

Then open <http://localhost:47821> in your browser. The `jarvis` CLI treats the
tunnelled port as local too, so `jarvis system status` just works.

**If you bind to a public address instead**, Jarvis refuses to start without a
control key — deliberately, because on a VPS the key is the only boundary
there is:

```bash
export JARVIS_BIND_HOST=0.0.0.0
```

Without a key that raises `Refusing to bind the Jarvis Control API to a
non-loopback address`. That is the guard working, not a bug. Generate a key
first, put a TLS-terminating reverse proxy in front, and never expose the raw
port. The tunnel is simpler and is what most people should use.

---

## 4. Voice: your laptop's microphone, the server's brain

The server has no microphone. The browser voice bridge is how you talk to it:
your browser captures the audio, streams it to `/ws/audio` over the WebSocket,
and the server runs speech-to-text, the brain and text-to-speech.

It is on by default (`[browser_voice] enabled`). Open the UI, allow the
microphone when the browser asks, and talk.

**The one thing that will trip you up:** browsers only hand out a microphone in
a *secure context* — HTTPS, or `localhost`. Over `http://your-vps-ip:47821`
your browser will silently refuse, and the UI will look like the microphone is
broken when the browser never granted it.

Two ways out, and the first is easier:

1. **The SSH tunnel from step 3.** The page is then served from `localhost`, so
   the browser counts it as secure and the microphone works. Nothing else to
   configure.
2. **A real certificate** on a reverse proxy (Caddy or nginx with Let's
   Encrypt) in front of the port. Worth it if several people use the box, or
   you want it reachable without a tunnel.

---

## 5. What degrades, and what goes quiet

This is the section that saves the most time, because the two big ones are
*supposed* to be silent and read as a broken install if you do not expect them.

**Local wake word — off.** "Hey Jarvis" listens to a microphone continuously.
There is no microphone, so there is nothing to listen to. Start a conversation
from the UI instead.

**Local speech-to-text — off.** The on-device recognizer is not in the base
install, and it would have no audio device to read from anyway. Cloud
speech-to-text does the work, which is why step 2 matters: with no key at all
you get a working chat UI and no voice.

**Text-to-speech plays in your browser, not on the server.** There are no
speakers on a VPS. The audio is streamed back to the page you have open.

**Screen control / computer use — unavailable.** It drives a real desktop.
There is no display, so the capability probe reports it as unavailable rather
than failing halfway through an action.

**The desktop window, the Orb overlay and the tray — not started.** `jarvis
serve` is the headless entry point and never tries to open a window. Running
bare `jarvis` on a VPS is the mistake to avoid: it looks for a display, does not
find one, and exits.

**Sound effects and the startup chime — silent no-ops.** They have no output
device and say nothing about them, which is correct here.

Everything else — chat, missions, the wiki, tasks, the scheduler, the control
API and CLI — behaves exactly as it does on a desktop.

---

## Checking the box before you trust it

```bash
# What this machine has, as JSON one object per line — parseable in a script.
jarvis --json --check   # note: --check first, then --json

# What is registered vs advertised. Exits non-zero on a hard failure,
# which makes it usable as an install gate.
jarvis --doctor --json
```

`--doctor` is the one to gate a deployment on. `status: "fail"` means something
advertised cannot work; `warn` is a degraded-but-fine state, which on a
headless box is normal for the voice-related entries.

---

## If something is wrong

| Symptom | Cause |
|---|---|
| The page loads, the microphone button does nothing | Not a secure context — see step 4. Use the SSH tunnel. |
| `Refusing to bind … without a control key` | You set a public `JARVIS_BIND_HOST`. Generate a control key, or use the tunnel. |
| Voice answers in the wrong language | The reply language is decided once per turn from your configured pin; set it in Settings rather than per provider. |
| `jarvis` exits immediately | You ran bare `jarvis`, which wants a display. Use `jarvis serve`. |
| The brain says no provider is configured | No key reached the process. A key in your shell is not a key in the systemd unit — put it in the unit file. |

Logs: `journalctl -u jarvis -f`.

---

## See also

- [`docs/jarvis-cli.md`](jarvis-cli.md) — driving a running instance from the
  terminal, including the remote/tunnel setup.
- [`docs/os-parity.md`](os-parity.md) — what differs per operating system, with
  the headless Linux column called out per capability.
