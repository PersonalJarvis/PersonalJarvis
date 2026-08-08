"""The local-model shortlist must name models that actually exist, today.

A curated list of model names is a maintenance liability: tags in the Ollama
library are retired, renamed and re-quantised, and a stale entry does not fail
loudly — it reaches a user as "Ollama does not know a model called 'qwen3.5:32b'"
at the moment they finally decided to download something. That is the worst
possible time to discover it, and it is invisible to every offline test, because
offline the shortlist is just a tuple of strings that parses fine.

This is the guard. It asks the public registry whether each curated id resolves,
and it is also what keeps the list CURRENT: a maintainer bumping the catalog to
a newer model generation finds out here whether the tags they wrote are real,
instead of shipping guesses.

Network-dependent by nature, so it is marked ``integration`` and self-skips when
the registry cannot be reached — an offline CI run or a developer on a train
must not go red over it. Run it explicitly with ``pytest -m integration``.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.brain.ollama_pull import RECOMMENDED_MODELS, ROLE_ORDER

pytestmark = pytest.mark.integration

_REGISTRY = "https://registry.ollama.ai/v2/library"
_ACCEPT = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}


def _manifest(client: httpx.Client, model: str) -> httpx.Response:
    name, _, tag = model.partition(":")
    return client.get(f"{_REGISTRY}/{name}/manifests/{tag or 'latest'}", headers=_ACCEPT)


@pytest.fixture(scope="module")
def registry() -> httpx.Client:
    with httpx.Client(timeout=15.0) as client:
        try:
            probe = _manifest(client, "bge-m3")
        except Exception as exc:  # noqa: BLE001 — offline is a skip, not a failure
            pytest.skip(f"Ollama registry unreachable: {type(exc).__name__} {exc}")
        if probe.status_code != 200:
            pytest.skip(f"Ollama registry answered {probe.status_code} for the probe")
        yield client


@pytest.mark.parametrize("entry", RECOMMENDED_MODELS, ids=lambda e: e.id)
def test_every_curated_model_exists_in_the_library(entry, registry) -> None:
    resp = _manifest(registry, entry.id)
    assert resp.status_code == 200, (
        f"'{entry.id}' is offered as a one-click download but the Ollama library "
        f"answered {resp.status_code}. Replace it with a tag that exists — a user "
        "clicking Download here gets a dead end otherwise."
    )


@pytest.mark.parametrize("entry", RECOMMENDED_MODELS, ids=lambda e: e.id)
def test_the_fallback_size_is_in_the_right_ballpark(entry, registry) -> None:
    """The curated size is the estimate shown until the registry answers.

    It only has to be close: the fit verdict compares it against the machine's
    memory, and an estimate that is off by half turns "fits in my card" into an
    eviction at load time. A wide tolerance keeps a re-quantised tag from
    failing the suite over a few hundred megabytes.
    """
    resp = _manifest(registry, entry.id)
    if resp.status_code != 200:
        pytest.skip("covered by the existence test above")
    real_gb = sum(int(layer.get("size") or 0) for layer in resp.json().get("layers", [])) / 1e9
    assert real_gb > 0
    assert abs(real_gb - entry.size_gb) <= max(1.0, real_gb * 0.25), (
        f"'{entry.id}' is listed at {entry.size_gb} GB but the library ships "
        f"{real_gb:.1f} GB. Update the curated estimate."
    )


def test_every_role_still_offers_something() -> None:
    """A role whose last entry is removed would silently stop being recommended
    — the panel would just have one fewer group, which reads as "not needed"."""
    roles = {entry.role for entry in RECOMMENDED_MODELS}
    assert roles == set(ROLE_ORDER)


def test_every_role_spans_more_than_one_size() -> None:
    """The whole point of the hardware probe is picking BETWEEN sizes. A role
    with a single entry gives it nothing to pick from, and a small machine and a
    workstation get the same answer again."""
    for role in ROLE_ORDER:
        sizes = {e.size_gb for e in RECOMMENDED_MODELS if e.role == role}
        assert len(sizes) >= 2, f"role '{role}' offers only one size"
