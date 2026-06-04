"""AP-16 parity guard for the wiki config sub-tables.

Every ``[memory.wiki.*]`` / wiki-related sub-table must carry
``ConfigDict(extra="allow")`` so a self-mod or drift-guard write of an
unknown future key survives validation *and* a model round-trip instead
of being silently dropped (or, under a stricter pre-validate mode,
blocking boot — AP-16).

``WikiContextConfig`` was the one wiki sub-table missing the marker while
all of its siblings carried it; this test pins the invariant for every
wiki sub-table so the inconsistency cannot regress.
"""
from __future__ import annotations

import pytest

# (class name, kwargs that satisfy any required fields)
_WIKI_SUBTABLES = [
    ("WikiContextConfig", {}),
    ("WikiCuratorConfig", {}),
    ("SessionRollupConfig", {}),
    ("SchedulerConfig", {}),
    ("VoiceBridgeConfig", {}),
    ("WikiMemoryConfig", {}),
    ("WikiIntegrationConfig", {}),
]


@pytest.mark.parametrize("class_name,base_kwargs", _WIKI_SUBTABLES)
def test_wiki_subtable_preserves_unknown_key(class_name: str, base_kwargs: dict) -> None:
    """extra='allow' keeps an unknown key; extra='ignore' would drop it.

    Asserting *preservation* (not merely "does not raise") is what makes
    this test actually distinguish ``allow`` from the pydantic default
    ``ignore`` — the latter accepts the key without error but discards it.
    """
    import jarvis.core.config as cfgmod

    model_cls = getattr(cfgmod, class_name)
    instance = model_cls.model_validate({**base_kwargs, "future_unknown_key": 7})
    dumped = instance.model_dump()
    assert dumped.get("future_unknown_key") == 7, (
        f"{class_name} dropped an unknown key — it is missing "
        f"ConfigDict(extra='allow') (AP-16)."
    )
