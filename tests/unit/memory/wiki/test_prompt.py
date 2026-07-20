"""Unit tests for ``jarvis.memory.wiki.prompt`` (Instance D)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.memory.wiki.prompt import (
    build_consolidator_prompt,
    build_system_prompt,
    build_user_prompt,
    compute_vault_summary,
    resolve_user_entity_slug,
    select_top_slugs,
)

# ---------------------------------------------------------------------
# Small in-memory VaultIndex fake — only ``pages_by_type`` is used.
# ---------------------------------------------------------------------


class _Page:
    """Minimal duck-typed page with a slug + page_type."""

    def __init__(self, slug: str, page_type: str) -> None:
        self.slug = slug
        self.page_type = page_type


class _FakeVault:
    """In-memory ``VaultIndex`` substitute for prompt tests."""

    def __init__(self, pages_by_type: dict[str, list[_Page]] | None = None) -> None:
        self._pages = pages_by_type or {}

    def pages_by_type(self, page_type: str) -> list[Any]:
        return list(self._pages.get(page_type, []))


# ---------------------------------------------------------------------
# select_top_slugs
# ---------------------------------------------------------------------


def test_select_top_slugs_empty_source_returns_empty() -> None:
    """A blank source contributes no tokens, so the shortlist is empty."""

    result = select_top_slugs("", ["ruben-luetke", "awareness-layer"])
    assert result == []


def test_select_top_slugs_zero_overlap_returns_empty() -> None:
    """When no candidate slug shares a token with the source, drop them all."""

    result = select_top_slugs("Phase B1 wiki curator", ["unrelated-thing"])
    assert result == []


def test_select_top_slugs_ranks_by_overlap_then_alpha() -> None:
    """Higher overlap wins; ties break alphabetically for deterministic output."""

    source = "ruben pushes the awareness layer for the jarvis-agent bridge"
    candidates = [
        "ruben-luetke",
        "awareness-layer",
        "jarvis-agent-bridge",
        "phase-6",
        "kontrollierer",
    ]
    ranked = select_top_slugs(source, candidates)
    assert "awareness-layer" in ranked
    assert "jarvis-agent-bridge" in ranked
    assert "ruben-luetke" in ranked
    assert "kontrollierer" not in ranked


def test_select_top_slugs_respects_limit() -> None:
    """The hard cap is honoured even when many candidates score."""

    source = " ".join(f"slug{i}" for i in range(30))
    candidates = [f"slug{i}" for i in range(30)]
    result = select_top_slugs(source, candidates, limit=10)
    assert len(result) == 10


def test_select_top_slugs_ignores_stopwords() -> None:
    """Common DE/EN function words don't dominate the ranking."""

    source = "der und die das the and the jarvis-agent bridge"  # i18n-allow: German/English stopword mix, content under test
    candidates = ["der-thing", "jarvis-agent-bridge"]
    result = select_top_slugs(source, candidates)
    assert result and result[0] == "jarvis-agent-bridge"


# ---------------------------------------------------------------------
# compute_vault_summary
# ---------------------------------------------------------------------


def test_compute_vault_summary_empty_vault() -> None:
    """An empty vault yields zero counts and an empty recent-log list."""

    vault = _FakeVault()
    summary = compute_vault_summary(vault)
    assert summary["counts"] == {
        "entity": 0,
        "concept": 0,
        "project": 0,
        "session": 0,
    }
    assert all(slugs == [] for slugs in summary["latest"].values())
    assert summary["recent_log"] == []


def test_compute_vault_summary_counts_and_samples(tmp_path: Path) -> None:
    """Page counts match; ``latest`` is alphabetical and capped at 5 slugs."""

    vault = _FakeVault({
        "entity": [_Page(f"e-{i:02d}", "entity") for i in range(7)],
        "concept": [_Page("awareness-layer", "concept")],
    })
    summary = compute_vault_summary(vault)
    assert summary["counts"]["entity"] == 7
    assert summary["counts"]["concept"] == 1
    assert summary["latest"]["entity"] == ["e-00", "e-01", "e-02", "e-03", "e-04"]
    assert summary["latest"]["concept"] == ["awareness-layer"]


def test_compute_vault_summary_reads_recent_log_entries(tmp_path: Path) -> None:
    """Three most recent ``## [...]`` headings are returned in chronological order."""

    log = tmp_path / "log.md"
    log.write_text(
        "# Wiki Log\n\n"
        "## [2026-05-11 18:00] create | first\n\n- a\n\n"
        "## [2026-05-11 18:30] update | second\n\n- b\n\n"
        "## [2026-05-11 19:00] update | third\n\n- c\n\n"
        "## [2026-05-11 19:30] update | fourth\n\n- d\n",
        encoding="utf-8",
    )
    summary = compute_vault_summary(_FakeVault(), log_path=log)
    assert len(summary["recent_log"]) == 3
    assert summary["recent_log"][-1].startswith("[2026-05-11 19:30]")


def test_compute_vault_summary_missing_log_is_silent(tmp_path: Path) -> None:
    """A non-existent log path degrades to an empty list, no raise."""

    summary = compute_vault_summary(_FakeVault(), log_path=tmp_path / "nope.md")
    assert summary["recent_log"] == []


def test_compute_vault_summary_resilient_to_vault_errors() -> None:
    """A vault that raises on ``pages_by_type`` still yields zero counts."""

    class _BoomVault:
        def pages_by_type(self, page_type: str) -> list[Any]:  # noqa: ARG002
            raise RuntimeError("vault is on fire")

    summary = compute_vault_summary(_BoomVault())
    assert summary["counts"]["entity"] == 0


# ---------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------


def test_build_system_prompt_includes_schema_verbatim() -> None:
    """The schema body must appear verbatim — no paraphrase, no normalisation."""

    schema = "---\ntype: meta\n---\n\n# Wiki Schema\n\nVerbatim spec.\n"
    prompt = build_system_prompt(schema, vault_summary=None)
    assert "Verbatim spec." in prompt
    assert "# Wiki Schema" in prompt


def test_build_system_prompt_appends_output_contract() -> None:
    """The JSON output contract is always part of the system prompt."""

    prompt = build_system_prompt("schema body", vault_summary=None)
    assert "Output Contract" in prompt
    assert '"operation"' in prompt
    assert "create" in prompt and "update" in prompt
    assert "smalltalk" in prompt.lower()


def test_build_system_prompt_includes_vault_summary_when_provided() -> None:
    """``compute_vault_summary``'s output is rendered into the prompt."""

    vault_summary = {
        "counts": {"entity": 2, "concept": 1, "project": 0, "session": 0},
        "latest": {
            "entity": ["ruben-luetke", "personal-jarvis"],
            "concept": ["awareness-layer"],
            "project": [],
            "session": [],
        },
        "recent_log": ["[2026-05-11 18:00] create | seed"],
    }
    prompt = build_system_prompt("schema body", vault_summary=vault_summary)
    assert "Entities: 2" in prompt
    assert "ruben-luetke" in prompt
    assert "Concepts: 1" in prompt
    assert "[2026-05-11 18:00] create | seed" in prompt


def test_build_system_prompt_handles_no_summary() -> None:
    """Passing ``None`` skips the snapshot section but keeps schema + contract."""

    prompt = build_system_prompt("schema body", vault_summary=None)
    assert "Current Vault Snapshot" not in prompt
    assert "Output Contract" in prompt


# ---------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------


def test_build_user_prompt_wraps_source_verbatim() -> None:
    """Source content is rendered between explicit BEGIN/END markers."""

    prompt = build_user_prompt(
        "BrainTurnCompleted 2026-05-11 19:42",
        "Ruben fixed BUG-019 in fix/bug-019-tts-silent.",
        top_slugs=["ruben-luetke"],
    )
    assert "Ruben fixed BUG-019 in fix/bug-019-tts-silent." in prompt
    assert "----- BEGIN SOURCE -----" in prompt
    assert "----- END SOURCE -----" in prompt
    assert "BrainTurnCompleted 2026-05-11 19:42" in prompt


def test_build_user_prompt_includes_top_slugs() -> None:
    """The keyword-overlap shortlist is rendered as bullet hints."""

    prompt = build_user_prompt(
        "source-label", "some content", top_slugs=["ruben-luetke", "jarvis-agent-bridge"],
    )
    assert "- ruben-luetke" in prompt
    assert "- jarvis-agent-bridge" in prompt


def test_build_user_prompt_handles_no_top_slugs() -> None:
    """The "no overlap" hint is emitted when the shortlist is empty."""

    prompt = build_user_prompt("source-label", "some content", top_slugs=[])
    assert "no overlap detected" in prompt


def test_build_user_prompt_handles_none_top_slugs() -> None:
    """``None`` is treated like an empty list (no crash on missing list)."""

    prompt = build_user_prompt("source-label", "content")
    assert "no overlap detected" in prompt


def test_build_user_prompt_demands_json_at_end() -> None:
    """The user message ends with the explicit JSON-array directive."""

    prompt = build_user_prompt("label", "content", top_slugs=[])
    assert prompt.rstrip().endswith("Return the JSON array now.")


# ---------------------------------------------------------------------
# Salience filter (B8): the system prompt must teach the LLM which
# inputs to drop ([]) and which to persist. We can't unit-test the LLM
# itself, but we can pin the prompt content so a future refactor
# cannot quietly delete the filter.
# ---------------------------------------------------------------------


def test_system_prompt_has_dedicated_salience_section() -> None:
    """The dedicated salience header must be present and listed first."""

    prompt = build_system_prompt("schema body", vault_summary=None)
    assert "Salience filter" in prompt
    # Read-first ordering: salience section appears before the output
    # contract block so the LLM sees the filter rules before the schema.
    assert prompt.find("Salience filter") < prompt.find("Output Contract")


def test_system_prompt_lists_smalltalk_to_drop() -> None:
    """All required smalltalk markers must appear in the empty-list block."""

    prompt = build_system_prompt("schema body", vault_summary=None).lower()
    # Greetings, status questions, tool-acks, smalltalk -- each category
    # must have at least one concrete example in the prompt so the LLM
    # has anchor words to pattern-match on.
    expected_smalltalk_markers = (
        "hallo",        # greeting
        "wie geht",     # status question
        "danke",        # ack as whole utterance
        "smalltalk",    # explicit category label
    )
    missing = [m for m in expected_smalltalk_markers if m not in prompt]
    assert not missing, f"missing smalltalk markers in salience block: {missing}"


def test_system_prompt_lists_fact_categories_to_persist() -> None:
    """All fact categories the curator must persist appear as bullet labels."""

    prompt = build_system_prompt("schema body", vault_summary=None).lower()
    # The six categories the user explicitly named in B8.9. Lowercased
    # matches so the test is whitespace/punctuation forgiving.
    expected_fact_markers = (
        "people",       # persons
        "dates",        # appointments
        "places",       # locations
        "preferences",  # likes / habits
        "decisions",    # from-now-on rules
        "project",      # active workstreams
        "relationship", # who-knows-whom
    )
    missing = [m for m in expected_fact_markers if m not in prompt]
    assert not missing, f"missing fact categories in salience block: {missing}"


def test_system_prompt_demands_empty_array_for_smalltalk() -> None:
    """Smalltalk inputs must produce `[]`, explicitly stated in the prompt."""

    prompt = build_system_prompt("schema body", vault_summary=None)
    # The "return []" directive lives in the salience block.
    assert "[]" in prompt
    salience_block = prompt.split("Output Contract")[0]
    assert "[]" in salience_block, (
        "the empty-array directive must appear inside the salience block, "
        "not only in the legacy output-contract rules below it"
    )


def test_system_prompt_carries_the_asymmetric_curation_bar() -> None:
    """Recall-protected for personal facts, precision-biased elsewhere."""

    prompt = " ".join(build_system_prompt("schema body", vault_summary=None).split())
    # Anchor phrases; if the wording shifts in future revisions, update
    # these assertions together with the prompt.
    assert "forgetting is the worse failure" in prompt
    assert "writing junk is the worse failure" in prompt
    assert "when in doubt with no strong personal anchor, skip it" in prompt


def test_system_prompt_requires_self_disclosure_for_topic_memory() -> None:
    """A request topic is not evidence of a lasting user relationship."""
    prompt = " ".join(build_system_prompt("schema body", vault_summary=None).split())

    assert '"What are the benefits of Vitamin D?"' in prompt
    assert '"Tell me about Monaco."' in prompt
    assert "both require `[]`" in prompt
    assert '"I own a yacht."' in prompt
    assert '"I plan to attend Monaco."' in prompt
    assert "Topic choice alone never qualifies" in prompt
    assert "first-person lived-experience report" in prompt


def test_system_prompt_has_no_maintainer_specific_user_target() -> None:
    """Generic curator guidance never bakes in one maintainer's profile slug."""
    prompt = build_system_prompt("schema body", vault_summary=None).lower()
    assert "entities/ruben.md" not in prompt
    assert "never infer a personal slug" in prompt


# ---------------------------------------------------------------------
# Stage-2 user-entity binding
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("", "user"),
        (None, "user"),
        ("../../private", "user"),
        ("Owner-Profile", "owner-profile"),
    ],
)
def test_resolve_user_entity_slug_is_safe_and_neutral(
    configured: object,
    expected: str,
) -> None:
    assert resolve_user_entity_slug(configured) == expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", "user"), ("owner-profile", "owner-profile")],
)
def test_consolidator_prompt_binds_exact_user_subject_and_page(
    configured: str,
    expected: str,
) -> None:
    candidate = SimpleNamespace(
        id=1,
        fact="The speaker prefers dark mode.",
        kind="preference",
        subjects=(expected,),
    )

    system, user = build_consolidator_prompt(
        [candidate],
        {},
        user_entity_slug=configured,
    )

    assert f'subject slug ["{expected}"]' in user
    assert f"profile page entities/{expected}.md" in user
    assert "never infer a name" in user
    assert "ruben" not in f"{system}\n{user}".lower()


def test_consolidator_prompt_rechecks_question_derived_user_claims() -> None:
    candidate = SimpleNamespace(
        id=7,
        fact="The user is interested in Vitamin D.",
        kind="preference",
        subjects=("user",),
        evidence_turn_id="vitamin-turn",
        evidence_excerpt=(
            "Evidence user turn [vitamin-turn]: "
            "What are the benefits of Vitamin D?"
        ),
    )

    system, user = build_consolidator_prompt([candidate], {})

    assert "evidence must assert or confirm that relationship" in system
    assert '"What are the benefits of Vitamin D?"' in system
    assert '"Tell me about Monaco."' in system
    assert 'both examples require "noop"' in system
    assert '"I own a yacht." and "I plan to attend Monaco."' in system
    assert "What are the benefits of Vitamin D?" in user
    # Rows without basis metadata render the explicit/3 defaults.
    assert "basis=explicit salience=3" in user


def test_consolidator_prompt_carries_the_graph_visibility_rule() -> None:
    candidate = SimpleNamespace(
        id=9,
        fact="The user is pursuing a high-end espresso machine.",
        kind="preference",
        subjects=("user", "espresso-machine"),
    )

    system, _user = build_consolidator_prompt([candidate], {})

    assert "Graph visibility (binding)" in system
    assert 'ALSO emit a secondary "add"' in system
    assert "cross-link it with the profile in both directions" in system
    assert "one-off mentions, smalltalk themes, or unsupported guesses" in system


def test_consolidator_prompt_elevates_explicit_persistence_requests() -> None:
    candidate = SimpleNamespace(
        id=8,
        fact="The user plans to travel to San Francisco tomorrow.",
        kind="plan",
        subjects=("user",),
        evidence_turn_id="sf-turn",
        evidence_excerpt=(
            "Evidence user turn [sf-turn]: Kannst du bitte hinzufügen, dass "  # i18n-allow
            "ich morgen nach San Francisco reisen möchte?"  # i18n-allow
        ),
    )

    system, user = build_consolidator_prompt([candidate], {})

    assert "Explicit persistence requests are binding" in system
    assert "English, German, and Spanish" in system
    assert 'MUST be "add" or "update"' in system
    assert "exact fact already" in system
    assert "unsupported by user evidence" in system
    assert "commands with no separately asserted durable content" in system
    assert "the one-shot action" in system
    assert "request itself remains non-durable" in system
    assert "San Francisco" in user


# ---------------------------------------------------------------------
# Integration-style assertion: round-trip through everything once.
# ---------------------------------------------------------------------


def test_build_full_prompt_roundtrip_smoke() -> None:
    """One full pipeline: vault → summary → system prompt + user prompt."""

    vault = _FakeVault({
        "entity": [_Page("ruben-luetke", "entity"), _Page("personal-jarvis", "entity")],
        "concept": [_Page("awareness-layer", "concept")],
    })
    summary = compute_vault_summary(vault)
    system_prompt = build_system_prompt("BINDING SCHEMA TEXT", summary)
    top_slugs = select_top_slugs("Ruben pushed the awareness layer", [
        "ruben-luetke", "awareness-layer", "personal-jarvis",
    ])
    user_prompt = build_user_prompt(
        "BrainTurnCompleted", "Ruben pushed the awareness layer", top_slugs,
    )

    assert "BINDING SCHEMA TEXT" in system_prompt
    assert "Entities: 2" in system_prompt
    assert "Concepts: 1" in system_prompt
    assert "ruben-luetke" in user_prompt
    assert "awareness-layer" in user_prompt
