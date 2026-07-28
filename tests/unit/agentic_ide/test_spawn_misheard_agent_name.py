"""A misheard CLI name must never cost the user a pane.

Live failure (Realtime voice session 2026-07-27 18:10): "Could you please open
two new Codex terminals and one Claude Code terminal?" reached the spawn parser
as "... and one **Cloude** code terminal". Nothing in the pattern matched that
word, so the group vanished before anything could report it: two Codex panes
opened, and the model — seeing three panes asked for and two names back —
invented a reason for the third ("that service is unavailable right now") that
no layer had ever reported.

Two properties are pinned here, and they pull against each other on purpose:

* a name spelled the way speech recognition heard it still opens its panes, and
* a spelling that is an ordinary English word ("cloud") still means nothing on
  its own — otherwise the fix would turn every sentence about the cloud into a
  request for a Claude pane.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent


def _groups(utterance: str) -> list[tuple[int, str | None]]:
    request = intent.detect_spawn(utterance)
    assert request is not None, utterance
    return [(g.count, g.agent) for g in request.groups]


def test_the_live_failure_opens_all_three_panes() -> None:
    """The verbatim transcript, down to the misspelling the log recorded."""
    assert _groups(
        "Could you please open two new Codex terminals and one Cloude code terminal?"
    ) == [(2, "codex"), (1, "claude")]


@pytest.mark.parametrize(
    "spelling",
    ["Claude", "Cloude", "Claud", "Clode", "Klaude"],
)
def test_every_accepted_spelling_reaches_the_same_cli(spelling: str) -> None:
    assert _groups(f"open two Codex terminals and one {spelling} terminal") == [
        (2, "codex"),
        (1, "claude"),
    ]


@pytest.mark.parametrize("spelling", ["Codex", "Kodex", "Codecs"])
def test_the_other_cli_is_spelled_by_ear_too(spelling: str) -> None:
    assert _groups(f"open three {spelling} terminals") == [(3, "codex")]


@pytest.mark.parametrize("spelling", ["Cloud", "Clawed", "Clod"])
def test_an_everyday_word_counts_only_with_the_products_second_word(
    spelling: str,
) -> None:
    """A pane needs "cloud code"; "cloud" on its own is not a CLI at all."""
    assert _groups(f"open two Codex terminals and one {spelling} Code terminal") == [
        (2, "codex"),
        (1, "claude"),
    ]
    # Same word, no "code" behind it: the pane is opened, but no CLI is named,
    # so the registry inherits the last pane's agent instead of guessing.
    assert _groups(f"open two {spelling} terminals") == [(2, None)]


def test_the_misspelling_survives_the_other_locales() -> None:
    """Spanish and German utterances go through the same table, not a copy."""
    german = "öffne zwei neue Kodex Terminals und drei Cloude Code Terminals"  # i18n-allow
    assert _groups(german) == [(2, "codex"), (3, "claude")]
    assert _groups("abre dos terminales de Codex y una terminal de Cloude Code") == [
        (2, "codex"),
        (1, "claude"),
    ]


def test_a_name_the_table_does_not_know_names_no_cli() -> None:
    """Unknown stays unknown — a near-miss must never become the wrong CLI."""
    assert intent._canonical_agent("gemini") is None
    assert intent._canonical_agent("Cloude Code") == "claude"
    # An ambiguous spelling means NOTHING on its own, in both directions. It is
    # not a request for a pane ("in the cloud"), and — since one such spelling
    # is also a verb this parser looks for — reading it as a product name would
    # take the sentence's own "open" away and drop the whole request.
    assert intent._canonical_agent("cloud") is None
    assert intent._canonical_agent("open") is None
    assert intent._canonical_agent("cloud code") == "claude"
    assert intent._canonical_agent("open code") == "opencode"
