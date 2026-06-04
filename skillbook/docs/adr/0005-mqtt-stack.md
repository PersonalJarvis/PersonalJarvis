# ADR-0005: MQTT stack — aiomqtt client + mocked bridge in tests

**Status:** Accepted
**Date:** 2026-05-26

## Context

The IP-Symcon (IPS) integration is described in the survey as an async MQTT subscriber receiving sensor state changes plus a JSON-RPC client issuing commands. The goal pre-decides: "MQTT: assume Mosquitto at localhost:1883; tests use embedded broker". And: "IP-Symcon: always mocked in tests".

Reading these together: production code should be able to connect to a real broker, but tests never assume a real IPS *or* a real broker. An "embedded broker" in Python (amqtt) is a 50+ MB dependency that would dominate skillbook's footprint.

## Decision

- Production MQTT client: **`aiomqtt`** (modern async wrapper around `paho-mqtt`), as an optional dependency `[mqtt]` extra. Lazy-imported.
- Tests substitute a **`MockBridge`** implementing the same `SymconBridge` interface but reading messages from an asyncio queue. No broker process, no extra dependency. The capstone scenario exercises the bridge via direct method calls — the MQTT wire format is never on the test critical path.
- The JSON-RPC client uses stdlib `urllib.request` for production and a function injection in tests (no `aiohttp` dep).

## Consequences

- Tests run with zero extra processes.
- Production users install `[mqtt]` and point at Mosquitto.
- The "embedded broker" wording from the goal is partially satisfied by the in-process mock; using a true embedded broker (amqtt) was rejected as over-weight for the capstone scope. ADR-0009 logs this as a survey/goal deviation.

## Alternatives considered

- **amqtt embedded broker**: works but adds ~25 transitive deps and several seconds of test boot; rejected for now, easy to add later.
- **gmqtt**: lighter than amqtt but less maintained.
- **paho-mqtt sync**: forces threading around an otherwise pure-async codebase; rejected.
