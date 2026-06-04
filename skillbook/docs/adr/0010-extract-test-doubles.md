# ADR-0010: Extract test doubles out of `src/skillbook/` into `tests/fakes/`

**Status:** Accepted
**Date:** 2026-05-26
**Supersedes:** parts of ADR-0004 (LLM provider strategy), ADR-0005 (MQTT stack), ADR-0006 (P2P transport) — specifically the framing that test doubles can ship inside the production package as "deterministic alternative implementations".

## Context

The self-audit committed at `skillbook/AUDIT.md` (branch `audit/self-review` @ 64257a54c) classified the original skillbook delivery as **(C) mock-shaped skeleton**: three test doubles (`MockLLM`, `MockSymconActor`, `InProcessTransport`) lived in `src/skillbook/`, and `AgentInstance.build()` instantiated `MockLLM()` as the default LLM, so the capstone scenario ran on mocks **by default** without any opt-in.

The verbal defense written into the original ADRs (e.g. `llm.py:8`: "MockLLM is NOT a stub: the canned source is a real, deterministic implementation") was structural sophistry. A class named `Mock*` that lives in production and is wired as the factory default for an integration boundary is a test double regardless of the docstring. The DoD check `grep -rn "TODO|FIXME|NotImplementedError"` did not catch it because the keywords looked nothing like the actual violation.

## Decision

1. **Move all test doubles out of `src/skillbook/`.**
   - `src/skillbook/ace_core/llm.py:MockLLM` → `tests/fakes/llm.py:FakeLLM`.
   - `src/skillbook/symcon_bridge/actor.py:MockSymconActor` → `tests/fakes/symcon.py:FakeSymconActor`.
   - `src/skillbook/p2p_sync/transport.py:InProcessTransport` → `tests/fakes/transport.py:InProcessTransport` (name retained — "InProcess" is descriptively accurate, not a Mock-prefix dodge).
   - Any future class matching `Mock*|Fake*|Stub*|Dummy*|InProcess*` in `src/` is to be moved on sight.
2. **Rename `Mock*` to `Fake*`.** Gerard Meszaros' xUnit Patterns vocabulary uses *Fake* for hand-written replacement implementations and *Mock* for behavior-verifying frameworks (e.g. `unittest.mock`). The original `Mock*` naming was misleading. Renaming is not cosmetic — it removes the temptation to grep `Mock` as a DoD check and call the constraint satisfied.
3. **Factories must take adapters as required arguments.** `AgentInstance.build()` now requires `llm`, `actors`, and `transport` as keyword arguments without `None` defaults. Missing arguments raise `MissingAdapterError` (defined in `src/skillbook/errors.py`).
4. **Production code may not import from `tests/`.** The structural enforcement of the Hard Constraint is `tests/test_no_mocks_in_production.py`, which greps `src/skillbook/` for the matching class definitions and any `from tests` imports. Both greps must return zero hits.
5. **`tests/fakes/` is excluded from pytest collection.** `pyproject.toml` adds `norecursedirs = ["tests/fakes"]` under `[tool.pytest.ini_options]` so the fakes are libraries for the tests, not tests themselves.

## Consequences

- The capstone test (`tests/test_capstone.py`) now wires every adapter explicitly. This is a one-line-per-adapter change at the test boundary and zero behavioral change in the closed loop — the audit's verdict that "the loop is mock-driven" is unchanged, but the **structural** violation it identified is removed.
- `default_llm()` in `src/skillbook/ace_core/llm.py` no longer falls back to a fake; it returns an Anthropic-backed LLM if `ANTHROPIC_API_KEY` and the `anthropic` SDK are available, otherwise it raises `MissingAdapterError`. Callers that want a deterministic offline LLM construct a `FakeLLM` from `tests/fakes/llm.py` explicitly.
- The `Transport` protocol remains in `src/skillbook/p2p_sync/transport.py`. Only the in-process implementation moves. A future real-network transport (TCP, libp2p) lands directly in `src/`.
- The audit's other findings — `lats.py` overselling MCTS, missing aiomqtt code, P2P is in-process — are out of scope for this ADR. ADR-0010 is structural only; functional gaps will be addressed in separate ADRs.

## Alternatives considered

- **Keep the doubles in `src/` and just rename `Mock*` → `Fake*`.** Rejected. The Hard Constraint is "test doubles do not live in production paths", not "test doubles are named carefully". Renaming alone leaves the structural violation intact.
- **Use `unittest.mock.MagicMock` in tests instead of writing fakes.** Rejected. The test-double behaviour is deterministic and stateful (`MockSymconActor` has a `failures_until_ok` counter; `MockLLM` emits canned Python source); `MagicMock` is the wrong primitive for that.
- **Ship a `[testing]` extras group with the fakes installed as `skillbook.testing.*`.** Rejected for this iteration: it would keep the doubles inside the importable package and re-open the DoD-grep loophole. Could be revisited if external consumers want to write tests against `AgentInstance` without copying `tests/fakes/` themselves.
