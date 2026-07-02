"""Computer-Use v2 — modular perceive->act->verify engine.

This package is the rebuilt Computer-Use engine ("v2"). It keeps the product
mechanism unchanged — a screenshot is the perception, mouse + keyboard are the
actuation — but fixes the structural defects of the legacy monolith
(`jarvis/harness/screenshot_only_loop.py`):

* one `CoordinateMapper` per captured frame, so every click resolves against
  the exact image the model saw (`geometry.py`),
* provider coordinate conventions as a capability, not a hardcoded 0-1000
  grid (`conventions.py`),
* platform-native actuation with landed-position read-back (`actuate/`),
* a closed perceive->act->verify state machine with an idempotency ledger
  that deterministically refuses duplicate actions (`loop.py`, `ledger.py`,
  `verify.py`).

Everything here is lazily imported by the harness engine resolver
(`jarvis/plugins/harness/computer_use.py::_resolve_run_cu_loop`) so boot time
is unaffected. The public entry point is `jarvis.cu.engine.run_cu_loop`, which
implements the exact same contract as the legacy engines.
"""
