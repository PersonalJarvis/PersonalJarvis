# Skillbook

Recursive, decentralized learning and memory layer for AI agents. Implements:

- **ACE** (Generator / Recursive Reflector / Curator) with delta-update skillbook.
- **Diagnostic guardrails** (AgentDoG + LATS rollback) for preemptive error blocking.
- **CRDT-based P2P sync** between agent instances (SHIMI-style hierarchical deltas over an injectable Transport).
- **IP-Symcon bridge** via async MQTT + JSON-RPC (mocked in tests).

The capstone scenario closes the loop end-to-end: actor timeout → guardrail block → Recursive Reflector trace analysis → skillbook delta → P2P sync → second instance proactively avoids the same timeout.

See `CLAUDE.md` for module boundaries, interfaces, and Definition of Done. ADRs live in `docs/adr/`.

## Quickstart

```bash
# from repo root
uv venv --python 3.12 skillbook/.venv
source skillbook/.venv/bin/activate    # or skillbook/.venv/Scripts/activate on Windows
uv pip install -e "skillbook[dev]"
pytest skillbook/ -x -q --seeds=5
```

When `ANTHROPIC_API_KEY` is unset, the LLM-driven reflection analysis falls back to a deterministic mock (see ADR-0004). The capstone test runs cleanly in both modes.
