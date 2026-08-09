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

from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ollama_library import parse_tags_html
from jarvis.brain.ollama_pull import RECOMMENDED_MODELS, ROLE_ORDER
from jarvis.core.config import MemoryConfig
from jarvis.dictation.polish_client import POLISH_FAMILIES
from jarvis.realtime.local_server import brain_link, tiers
from jarvis.ultrawiki.embedding_models import CURATED_EMBEDDING_MODELS
from jarvis.ultrawiki.embeddings import DEFAULT_MODELS

pytestmark = pytest.mark.integration

_REGISTRY = "https://registry.ollama.ai/v2/library"
_ACCEPT = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}

_RUNTIME_OLLAMA_MODELS = tuple(
    sorted(
        {
            AckBrainConfig().providers.ollama.model,
            MemoryConfig().embedding_model,
            DEFAULT_MODELS["ollama"],
            next(f.default_model for f in POLISH_FAMILIES if f.id == "ollama"),
            *(model for model, _label in CURATED_EMBEDDING_MODELS["ollama"]),
            *(tier.brain_model for tier in tiers.TIERS),
            *brain_link._PREFERRED_MODELS,
        }
    )
)


def _manifest(client: httpx.Client, model: str) -> httpx.Response:
    name, _, tag = model.partition(":")
    return client.get(f"{_REGISTRY}/{name}/manifests/{tag or 'latest'}", headers=_ACCEPT)


@pytest.fixture(scope="module")
def registry() -> httpx.Client:
    with httpx.Client(timeout=15.0) as client:
        try:
            probe = _manifest(client, "qwen3-embedding:4b")
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


@pytest.mark.parametrize("entry", RECOMMENDED_MODELS, ids=lambda e: e.id)
def test_curated_model_is_not_a_year_old(entry, registry) -> None:
    """General recommendations must stay on a current official artifact."""
    _assert_model_is_current(entry.id, registry)


@pytest.mark.parametrize("model_id", _RUNTIME_OLLAMA_MODELS)
def test_runtime_default_is_not_a_year_old(model_id, registry) -> None:
    """Hidden defaults must obey the same currency rule as the visible list."""
    _assert_model_is_current(model_id, registry)


def _assert_model_is_current(model_id: str, registry: httpx.Client) -> None:
    name, _, requested_tag = model_id.partition(":")
    response = registry.get(f"https://ollama.com/library/{name}/tags")
    assert response.status_code == 200
    tags = parse_tags_html(response.text, name)
    tag = requested_tag or "latest"
    selected = next((item for item in tags if item["tag"] == tag), None)
    assert selected is not None, f"'{model_id}' is absent from the public tag page"
    updated = selected["updated"]
    assert updated and "year" not in updated, (
        f"'{model_id}' is shown as '{updated}' in the official library. Replace "
        "year-old general recommendations with a current hardware-fitting model."
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
