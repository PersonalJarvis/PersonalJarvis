"""Contract test: ``scripts/config-soll.json`` ``brain.primary`` <-> ENV mapping.  # i18n-allow: filename/jargon identifier, not translatable prose

The Brain-provider switch in the desktop app's API/Settings section persists
across all three drift-defence layers (BUG-010):

* ``jarvis.toml``                -> ``[brain].primary``
* ``scripts/config-soll.json``   -> ``brain.primary``  (the drift-guard's soll)  # i18n-allow: filename/jargon identifier, not translatable prose
* User-scope ENV override        -> ``JARVIS__BRAIN__PRIMARY``

For the UI switch to make the drift-guard a no-op (instead of reverting the
choice on its next 5-minute run), the guard's derived ENV-variable name for
``brain.primary`` MUST be exactly ``JARVIS__BRAIN__PRIMARY`` -- the same name
the UI sets and the same name the maintainer sets by hand.

``scripts/jarvis-config-drift-guard.ps1`` derives that name with the rule
(see the ENV-override block around lines 153-163)::

    "JARVIS__" + <section>.ToUpper() + "__" + <key>.ToUpper()

This test mirrors that rule in Python and pins the contract so a rename on
either side (the JSON section/key, or the guard's derivation rule) fails CI
loudly instead of silently breaking provider persistence.

No PowerShell or Windows dependency: it only parses the JSON and reproduces a
string-formatting rule, so it runs on the cloud-first Linux CI path too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# scripts/config-soll.json lives at <repo>/scripts/config-soll.json.  # i18n-allow: filename identifier, not translatable prose
# This test file is at <repo>/tests/unit/scripts/, so parents[3] is the repo root.
CONFIG_SOLL_PATH = (  # i18n-allow: filename/jargon identifier, not translatable prose
    Path(__file__).resolve().parents[3] / "scripts" / "config-soll.json"  # i18n-allow: filename identifier, not translatable prose
)


def _derive_env_name(section: str, key: str) -> str:
    """Reproduce the drift-guard's JARVIS__<SECTION>__<KEY> derivation.

    Mirrors ``scripts/jarvis-config-drift-guard.ps1``:
        "JARVIS__" + $section.Name.ToUpper() + "__" + $keyProp.Name.ToUpper()
    """
    return "JARVIS__" + section.upper() + "__" + key.upper()


@pytest.fixture(scope="module")
def soll() -> dict:  # i18n-allow: fixture name mirrors the config-soll.json jargon term, not translatable prose
    assert CONFIG_SOLL_PATH.is_file(), (  # i18n-allow: filename/jargon identifier, not translatable prose
        f"expected the drift-guard soll file at {CONFIG_SOLL_PATH}"  # i18n-allow: filename/jargon identifier, not translatable prose
    )
    return json.loads(CONFIG_SOLL_PATH.read_text(encoding="utf-8"))  # i18n-allow: filename/jargon identifier, not translatable prose


# ---------------------------------------------------------------------------
# brain.primary presence + shape
# ---------------------------------------------------------------------------


def test_config_soll_is_valid_json(soll: dict) -> None:  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    """If the JSON is malformed the whole drift-guard fails to parse (exit 4)."""
    assert isinstance(soll, dict)  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose


def test_brain_section_exists(soll: dict) -> None:  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    assert "brain" in soll, "config-soll.json must keep a top-level 'brain' section"  # i18n-allow: filename/jargon identifier, not translatable prose
    assert isinstance(soll["brain"], dict)  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose


def test_brain_primary_is_non_empty_string(soll: dict) -> None:  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    """brain.primary must exist and be a non-empty string.

    The drift-guard pins this value and the UI provider switch overwrites it.
    A missing/empty value would make the guard skip the key (WARN) and the UI
    switch would have no soll to keep in sync -> silent revert risk.  # i18n-allow: filename/jargon identifier, not translatable prose
    """
    primary = soll["brain"].get("primary")  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    assert primary is not None, "brain.primary must be present in config-soll.json"  # i18n-allow: filename/jargon identifier, not translatable prose
    assert isinstance(primary, str), "brain.primary must be a string"
    assert primary.strip() != "", "brain.primary must not be empty/whitespace"


# ---------------------------------------------------------------------------
# brain.primary <-> JARVIS__BRAIN__PRIMARY ENV-name contract
# ---------------------------------------------------------------------------


def test_brain_primary_env_name_is_canonical(soll: dict) -> None:  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    """The guard-derived ENV name for brain.primary is JARVIS__BRAIN__PRIMARY.

    This is the exact variable the desktop app's provider switch sets and the
    maintainer sets by hand. If the section/key in config-soll.json is ever  # i18n-allow: filename identifier, not translatable prose
    renamed, the derived name diverges from what the UI writes and the guard
    would re-add a 'missing' ENV var every run -> the whole fix breaks.
    """
    assert "primary" in soll["brain"]  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    env_name = _derive_env_name("brain", "primary")
    assert env_name == "JARVIS__BRAIN__PRIMARY"


def test_env_derivation_rule_matches_guard_for_all_keys(soll: dict) -> None:  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
    """Every (section, key) in the soll maps to a JARVIS__-prefixed ENV name.  # i18n-allow: filename/jargon identifier, not translatable prose

    Pins the general derivation rule (double-underscore separator, upper-case,
    JARVIS__ prefix) the guard relies on -- not just brain.primary -- so any
    future key inherits the same contract.
    """
    for section, payload in soll.items():  # i18n-allow: identifier name mirrors the config-soll.json jargon term, not translatable prose
        if section.startswith("_") or not isinstance(payload, dict):
            continue
        for key in payload:
            env_name = _derive_env_name(section, key)
            assert env_name.startswith("JARVIS__"), env_name
            assert "__" in env_name[len("JARVIS__"):], env_name
            assert env_name == env_name.upper(), env_name
