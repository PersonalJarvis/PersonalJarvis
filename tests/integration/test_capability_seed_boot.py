"""Boot-wiring regression: the brain build must seed the CapabilityRegistry.

The 2026-05-25 live bug ("Kannst du mir einen Subagent spawnen …" → Jarvis
answers "Das kann ich noch nicht. Mir fehlt dafür ein Werkzeug …") was caused by  # i18n-allow
``seed_registry()`` never being called in the production boot path. The registry
stayed empty, so ``BrainManager._check_unsupported_intent`` rejected every action
utterance before the deterministic force-spawn path could run.

The existing capability tests (``test_capability_coupling_e2e.py``) seed the
registry themselves in a fixture, so they proved the gate LOGIC but never proved
the production WIRING. This file closes that gap: it clears the singleton to the
true boot state and asserts the brain build populates it.
"""
from __future__ import annotations

import pytest

from jarvis.brain.factory import build_default_brain
from jarvis.core.capabilities import get_registry
from jarvis.core.capabilities_seed import seed_registry


@pytest.fixture()
def empty_registry():
    """Reset the process-wide registry to the real (empty) boot state, then
    restore a seeded registry afterwards so later tests are not polluted.

    The production brain build under test also installs the process-global
    SkillContext (loaded with whatever skills the host machine carries).
    Left in place, it made ``_should_force_spawn``'s installed-skill guard
    stand down in UNRELATED later tests — the routing suite's
    'Wie viele PRs sind in jarvis-repo offen?' spawn  # i18n-allow: quoted fixture utterance
    params failed in every full run while staying green file-scoped. Restore
    the pre-test context so the build's side effects end with this test.
    """
    from jarvis.skills.skill_context import (
        set_skill_context,
        try_get_skill_context,
    )

    reg = get_registry()
    reg._caps.clear()  # noqa: SLF001 — deliberately simulate fresh boot
    prior_skill_ctx = try_get_skill_context()
    try:
        yield reg
    finally:
        seed_registry(reg)  # leave a populated registry for downstream tests
        set_skill_context(prior_skill_ctx)


def test_build_default_brain_seeds_capability_registry(empty_registry) -> None:
    """After the brain is built, the registry must be populated AND a
    sub-agent spawn request must resolve to ``tool.spawn-worker`` (so the
    unsupported-intent gate steps aside instead of refusing)."""
    assert empty_registry.all() == (), "precondition: registry starts empty"

    # Build the production brain. The seed must happen regardless of whether
    # later brain-build steps degrade (no API keys in CI, etc.).
    try:
        build_default_brain(tier="router")
    except Exception as exc:  # noqa: BLE001 — brain build robustness is not under test here
        # The seed must run regardless; a degraded build (no API keys in CI) is fine.
        print(f"build_default_brain degraded (expected in CI): {exc!r}")

    assert empty_registry.all(), (
        "build_default_brain must seed the CapabilityRegistry at boot — "
        "an empty registry makes _check_unsupported_intent refuse every action"
    )

    resolved = empty_registry.resolve_intent(
        "Kannst du mir einen Subagent spawnen, der eine Datei macht"  # i18n-allow
    )
    assert resolved is not None, (
        "a sub-agent spawn request must resolve to a registered capability"
    )
    assert resolved.id == "tool.spawn-worker", (
        f"expected tool.spawn-worker, got {resolved.id!r}"
    )
