# The dev instance — two desktop apps from one checkout

**Problem.** Seeing a backend change means restarting the desktop app, and a
restart takes the Agentic-IDE panes, their coding-agent sessions and the voice
stack down with it. Restoring them afterwards is the most annoying part of
working on the app from inside the app.

**Answer.** Run two desktop apps from the same working tree:

| | **Personal Jarvis** (default instance) | **Personal Jarvis Dev** (dev instance) |
|---|---|---|
| Purpose | The app you live in: voice, hotkeys, channels, the Agentic-IDE sessions you vibe-code from. Never restarted for a code change. | A second window you restart as often as you like to look at the current working tree. |
| Start | `run.bat` / `./run.sh`, the Start-Menu / Desktop shortcut, autostart | `run-dev.bat` / `./run-dev.sh`, the **Personal Jarvis Dev** shortcut, or `python -m jarvis.ui.web.launcher --instance dev` |
| Frontend | The built bundle; a rebuilt bundle is picked up by the window itself (`bundleWatch`) | Same built bundle, same pick-up — so UI changes show up in BOTH without a restart |
| Backend (Python) changes | Only after a restart — which you now do on the dev app | Restart from the tray / in-app Restart / the launcher as usual |
| Data directory | `data/` | `data-dev/` (created on first start, seeded once with `setup_state.json`, `identity_card.json`, `core_memory.json` and a snapshot of `chats.db`) |
| Ports | `[ui].admin_api_port` etc. as configured (47821 …) | every configured port **+100** (47921 …) |
| Config, keys, agent accounts, skills | shared (`jarvis.toml`, keyring, `%LOCALAPPDATA%\Jarvis` / `~/.jarvis`) | shared — same providers, same logins, so "everything works" |
| Window title / taskbar / tray | Personal Jarvis, mascot icon | Personal Jarvis **Dev**, mascot with a yellow **DEV** tag, own taskbar group, own tray entry; the sidebar shows a small **DEV** badge |
| Wake word, global hotkeys | as configured | **off** — the microphone and the key combos belong to the default app (the in-window mic button and chat still work) |
| Chat channels (Telegram/Discord), autostart | as configured | **off** — a second poller on the same bot token would collide; the dev app never touches the login autostart entry |
| Agentic-IDE resume offer | `last_session.json` | `last_session.dev.json` — a restarted dev app never offers to resume panes that are alive in the default app |

## How it is selected

`JARVIS_INSTANCE=dev` in the environment, or `--instance dev` on the launcher
(which sets the variable before anything else is imported). The variable is a
*single* underscore on purpose: the relauncher refreshes every `JARVIS__*`
config override on an in-app restart, and the instance must survive exactly
that restart. Children — the relauncher, the branded re-exec, the unelevated
copy, the in-app restart chain — inherit it.

Only `default` and `dev` are known names; anything else fails the boot with a
sentence, because a typo must not start a second *default* app that fights the
live one over its lock and port.

`jarvis/core/instance.py` is the one place that knows what differs; everything
else asks it (`current_instance()`): `jarvis.core.config.DATA_DIR`,
`load_config` (port offsets, `memory.data_dir`), `jarvis.ui.icon_utils` (AUMID,
shortcut, branded exe, icon), `jarvis.ui.desktop_app.WINDOW_TITLE`, the tray,
`jarvis.setup.state`, the Agentic-IDE resume store, `SpeechPipeline` (wake word,
hotkeys), the Friends stack (channels), and the launcher (autostart).

## Shortcuts

```
python scripts/install_shortcuts.py --dev
```

writes `Desktop\Personal Jarvis Dev.lnk` (DEV icon, `--instance dev`, its own
AppUserModelID). The first dev start also writes the Start-Menu entry
`Personal Jarvis Dev` through the same code path the default app uses, so the
taskbar button is named and iconed from the first launch.

The dev icon is rendered by `scripts/make_dev_icon.py` into both icon homes
(`jarvis/assets/icons/` ships with the package, `assets/icons/` is the
build-tool copy).

## Known sharing

Two things are deliberately shared and fine: `jarvis.toml` (settings you change
in the dev app apply to the default app too — same user, same preferences) and
the per-user directory (agent accounts, skills, board, recents). A few
CWD-relative paths still point at `data/` from both instances — the flight
recorder, the wiki curator lock (a cross-process lock, so this is correct), the
review / self-mod audit logs, computer-use blobs. They are logs and locks, not
stores; none of them makes the two apps answer for each other.

## Cross-platform

Built OS-neutral; the Windows identity layer is the one verified live. See
`docs/os-parity.md` P-37 for macOS (no separate bundle, dock icon follows the
default app) and Linux (own `.desktop` entry, unverified live).
