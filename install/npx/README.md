# `npx personal-jarvis`

A one-command installer for **Personal Jarvis** that works the same on Windows,
macOS and Linux:

```bash
npx personal-jarvis              # install the full desktop app
npx personal-jarvis --headless   # server / no-GUI install
```

It detects your OS and hands off to the canonical Stage-1 installer
(`install/install.ps1` on Windows, `install/install.sh` on macOS/Linux) — the
exact same path as the README one-liners. Extra flags (`--headless`,
`--no-launch`, `--no-wizard`, `--no-voice-local`) are forwarded.

## Publishing (maintainer)

`npx personal-jarvis` resolves to the npm package named `personal-jarvis`, so it
must be published once:

```bash
cd install/npx
npm publish --access public
```

Before publishing it's already usable straight from GitHub:

```bash
npx github:personal-jarvis/PersonalJarvis#main install/npx   # (or a tag)
```

Environment overrides (honoured by the bin and the underlying installer):

| Var | Effect |
|---|---|
| `JARVIS_INSTALL_REPO` | install from a fork (`owner/repo`) |
| `JARVIS_INSTALL_REF`  | install from a branch/tag instead of `main` |
| `JARVIS_INSTALL_DIR`  | install location (default `~/.personal-jarvis`) |

No runtime dependencies — pure Node ≥ 18 plus your platform's own shell.
