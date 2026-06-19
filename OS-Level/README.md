# OS-Level Edge-Glow Overlay

A separate Python process for Personal Jarvis that draws an animated glow border at the screen edge when the main Jarvis performs interactive PC actions (mouse, keyboard). Plus a mascot, click ripple, cursor trail, and typing indicator.

Full spec: `OS-LEVEL_PLAN.md` (Phase 9).

## Installation

```powershell
cd OS-Level
pip install -e . --no-deps
```

## Smoke test

```powershell
python -m overlay --self-test     # Foundation check, exit 0
python -m overlay --smoke         # Verify visually for 5 sec
```

## Status

Phase 9.1 — foundation and transparent click-through windows. A yellow 50x50px test square in the top right serves as a visual marker; it disappears in Phase 9.4 when the real glow render pipeline arrives.
