"""Regression guard: the sub-agent worker model must never silently drift to an
approved-access-only / unreachable model (BUG-010 config-drift class).

History (2026-06-14): the heavy-worker model was migrated claude-fable-5 ->
claude-opus-4-8 because the Claude Max subscription lost CLI access to Fable
("Claude Fable 5 is currently unavailable", 404 model_not_found). The migration
updated ``config-soll.json`` (the drift-guard's desired state) and the Python  # i18n-allow
default ``_DEFAULT_CLAUDE_MODEL`` — but NOT the value the resolver actually reads
first, ``[brain.sub_jarvis].model`` in ``jarvis.toml``. ``jarvis.toml`` is
read-only and nothing reconciled the approved value down into it, so the two
files silently disagreed: soll said opus, toml said fable, and every worker  # i18n-allow
spawned ``claude --model claude-fable-5`` -> 404 -> ``task_error``. The fix went
into a layer the live path never consults, which is why "the Fable bug came
back".

These tests fail loudly the moment ``jarvis.toml`` and ``config-soll.json``  # i18n-allow
disagree on the worker/critic model pins, or the moment an unreachable model is
pinned in the tracked source-of-truth. The existing
``test_subagent_model_route.py`` mocks the config writer and so could never have
caught this — it never compares the real files.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JARVIS_TOML = _REPO_ROOT / "jarvis.toml"
_CONFIG_SOLL = _REPO_ROOT / "scripts" / "config-soll.json"  # i18n-allow

# Models the Claude Max subscription cannot reach via the ``claude`` CLI. Pinning
# any of these on the worker/critic path is a guaranteed 404 -> task_error.
# (Maintainer decision 2026-06-14: Fable is approved-access-only.)
_UNREACHABLE_CLAUDE_MODELS = frozenset({"claude-fable-5"})

_MODEL_KEYS = (
    # Renamed from brain.sub_jarvis → brain.worker in the 2026-06-29
    # Jarvis-Agents rename; both the TOML section header and the
    # config-soll.json flat key now use "brain.worker".  # i18n-allow
    ("brain.worker", "model"),
    ("brain.providers.claude-api", "deep_model"),
)


def _load_soll() -> dict:  # i18n-allow
    return json.loads(_CONFIG_SOLL.read_text(encoding="utf-8-sig"))  # i18n-allow


def _load_toml() -> dict:
    # utf-8-sig strips the BOM the BOM-safe writer leaves on jarvis.toml.
    return tomllib.loads(_JARVIS_TOML.read_text(encoding="utf-8-sig"))


def _soll_value(soll: dict, dotted: str, key: str) -> str | None:  # i18n-allow
    block = soll.get(dotted)  # i18n-allow
    if block is None:  # some entries are nested instead of dotted
        cur: object = soll  # i18n-allow
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        block = cur if isinstance(cur, dict) else None
    if not isinstance(block, dict):
        return None
    return block.get(key)


# Back-compat map: renamed TOML section → old section name.  During the
# 2026-06-29 Jarvis-Agents rename, jarvis.toml installs on disk may still
# carry the OLD section header ([brain.sub_jarvis]) alongside the NEW one
# ([brain.worker]).  The config system aliases them; the raw TOML reader here
# does not — so we fall back to the old name when the new section lacks the key.
_TOML_SECTION_ALIASES: dict[str, str] = {
    "brain.worker": "brain.sub_jarvis",
}


def _toml_value(toml: dict, dotted: str, key: str) -> str | None:
    cur: object = toml
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if not isinstance(cur, dict):
        return None
    val = cur.get(key)
    if val is None and dotted in _TOML_SECTION_ALIASES:
        # Transition period: key absent in the new section — try the old one.
        val = _toml_value(toml, _TOML_SECTION_ALIASES[dotted], key)
    return val


def test_soll_worker_model_pins_are_reachable() -> None:  # i18n-allow
    """The tracked drift-guard source-of-truth must not pin an unreachable model.

    config-soll.json ships in git (cloud-first), so this always runs — including  # i18n-allow
    on a fresh VPS checkout — and guards against re-pinning Fable in the soll.  # i18n-allow
    """
    soll = _load_soll()  # i18n-allow
    for dotted, key in _MODEL_KEYS:
        value = _soll_value(soll, dotted, key)  # i18n-allow
        assert value not in _UNREACHABLE_CLAUDE_MODELS, (
            f"config-soll.json {dotted}.{key} = {value!r} is approved-access-only "  # i18n-allow
            f"and unreachable via the Claude Max CLI; every worker/critic spawn "
            f"would 404 -> task_error. Pin a reachable model (e.g. claude-opus-4-8)."
        )


@pytest.mark.skipif(
    not _JARVIS_TOML.exists(),
    reason="no local jarvis.toml (fresh checkout / headless VPS) — soll guard covers it",  # i18n-allow
)
def test_jarvis_toml_model_pins_match_soll() -> None:  # i18n-allow
    """``jarvis.toml`` (what ``load_config`` reads) must agree with the approved
    ``config-soll.json`` on every worker/critic model pin.  # i18n-allow

    This is the exact invariant that broke on 2026-06-14: soll updated to  # i18n-allow
    claude-opus-4-8, jarvis.toml left stale at claude-fable-5. Because the
    resolver reads jarvis.toml first and returns it verbatim, the stale value
    won and the mission 404'd. A 3-layer ``config_writer.set_sub_jarvis_model``
    keeps them in lock-step; a raw TOML-only edit (or a drift-guard that never
    ran) reintroduces the split this test catches.
    """
    soll = _load_soll()  # i18n-allow
    toml = _load_toml()
    mismatches: list[str] = []
    for dotted, key in _MODEL_KEYS:
        toml_val = _toml_value(toml, dotted, key)
        soll_val = _soll_value(soll, dotted, key)  # i18n-allow
        # A missing pin on either side must FAIL — otherwise a renamed/typo'd key
        # that vanishes from BOTH files makes `None == None` pass vacuously and
        # the guard silently stops guarding (the very BUG-010 regression it
        # exists to catch).
        if toml_val is None or soll_val is None:  # i18n-allow
            mismatches.append(
                f"  {dotted}.{key}: missing pin (jarvis.toml={toml_val!r}, "
                f"config-soll={soll_val!r}) — both must define it"  # i18n-allow
            )
        elif toml_val != soll_val:  # i18n-allow
            mismatches.append(
                f"  {dotted}.{key}: jarvis.toml={toml_val!r} != config-soll={soll_val!r}"  # i18n-allow
            )
    assert not mismatches, (
        "jarvis.toml drifted from config-soll.json on worker/critic model "
        "pins — the live resolver reads jarvis.toml, so the soll-approved fix "
        "never reached the running worker:\n" + "\n".join(mismatches)
    )


@pytest.mark.skipif(
    not _JARVIS_TOML.exists(),
    reason="no local jarvis.toml (fresh checkout / headless VPS)",
)
def test_jarvis_toml_worker_model_is_reachable() -> None:
    """Defense-in-depth: the live worker model pin itself must be reachable."""
    toml = _load_toml()
    worker_model = _toml_value(toml, "brain.worker", "model")
    assert worker_model not in _UNREACHABLE_CLAUDE_MODELS, (
        f"jarvis.toml [brain.worker].model = {worker_model!r} is unreachable "
        f"via the Claude Max CLI — every sub-agent worker will 404 -> task_error."
    )
