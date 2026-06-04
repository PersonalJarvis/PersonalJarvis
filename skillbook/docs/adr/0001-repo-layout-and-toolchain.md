# ADR-0001: Repository layout and toolchain

**Status:** Accepted
**Date:** 2026-05-26

## Context

The goal requires Python 3.12, `uv` for dependency management, a `pyproject.toml`, and `.venv/` in the skillbook root. The codebase will host five modules (ace-core, symcon-bridge, memory-layer, guardrails, p2p-sync) that must not pollute the parent Personal-Jarvis package.

## Decision

- Use **`src/skillbook/` layout** with one sub-package per module. This guarantees that local `import skillbook` always resolves to the in-tree code and not an accidentally globally-installed wheel.
- Use **`uv`** for venv creation and dependency resolution. The verification command (`pytest skillbook/ -x -q --seeds=5`) presumes a venv where `pytest` and the skillbook package are installed; `uv venv --python 3.12 .venv && uv pip install -e .[dev]` reaches that state.
- Set **`requires-python = ">=3.11"`** in `pyproject.toml`. Python 3.12 is the preferred runtime per the goal, but the code uses no 3.12-only syntax features, and 3.11 is what is locally available. uv will fetch 3.12 if requested; either runtime satisfies the test suite. (Documented for future grep: this is a deliberate relaxation of the goal's "Python 3.12, settled" line — see ADR-0009.)
- **`tests/`** is a sibling of `src/`, not nested inside the package. `conftest.py` sits at the skillbook root and prepends `src/` to `sys.path` so tests can `import skillbook.*` without an editable install — but the editable install is still the recommended workflow.

## Consequences

- Re-running the capstone from a fresh checkout requires `uv pip install -e skillbook[dev]` (or `pip install -e ./skillbook[dev]` for non-uv users). Documented in README.
- No package can import another's `tests/` content — `tests/` is outside `src/skillbook/` deliberately.
- Future modules added inside `src/skillbook/` get test discovery for free.

## Alternatives considered

- **Flat-package layout** (`skillbook/{module}/`): rejected because parent dir already has many sub-packages and import collisions are a known recurring class of bug in Personal Jarvis (see parent BUG-006 four-layer restore trap).
- **Editable install only, no `sys.path` fallback**: rejected because the verification command should work even before `uv pip install -e .` runs, to keep the smoke loop trivial.
