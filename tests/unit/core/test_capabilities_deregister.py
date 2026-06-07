from jarvis.core.capabilities import Capability, CapabilityRegistry


def _cap(cid: str) -> Capability:
    return Capability(
        id=cid, source="mcp", verbs=("lies",), objects=("postfach",),
        description="x", risk_tier="ask", requires_evidence=True,
    )


def test_deregister_removes_capability():
    reg = CapabilityRegistry()
    reg.register(_cap("plugin.gmail"))
    assert reg.resolve_intent("lies mein postfach") is not None
    reg.deregister("plugin.gmail")
    assert reg.resolve_intent("lies mein postfach") is None


def test_deregister_unknown_is_noop():
    reg = CapabilityRegistry()
    reg.deregister("does.not.exist")  # must not raise
