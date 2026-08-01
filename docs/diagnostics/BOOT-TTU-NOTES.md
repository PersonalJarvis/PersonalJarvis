# Boot and time-to-usable contract

This document records the public startup-performance contract. Private machine
traces and workstation-specific measurements are intentionally not included.

## Required milestones

- `APP_INTERACTIVE`: the desktop shell can accept input.
- `VOICE_USABLE`: the normal speech path can accept a turn or report an honest
  capability limitation.
- boot observation window: no more than 8 seconds for the CI budget sample.
- app-interactive and voice-usable: no more than 20 seconds on the supported
  reference path.

The executable gate is `scripts/ci/check_boot_budget.py`.

## Architecture rule

Heavy or optional subsystems must not initialize on the startup critical path.
They start through the deferred background phase after the interactive and
voice milestones. Module-level heavy imports, synchronous network discovery,
model loading, and optional database setup before these milestones are defects.

Routes for a warming subsystem return an honest unavailable or warming result;
they do not block startup. Headless Linux must reach its usable milestone even
when desktop, audio, GPU, keyring, or optional-provider capabilities are absent.

## Regression procedure

1. Run the boot-budget gate in a clean environment.
2. Compare milestone timestamps, not wall-clock impressions.
3. Identify the first new synchronous import or initialization step.
4. move optional work to the deferred registry or post-ready task;
5. rerun the targeted startup tests and the full non-slow suite.

Do not commit raw boot traces that contain usernames, home paths, device names,
credentials, conversation content, or machine identifiers.
