"""The router prompt routes "agent" to the society, not to a mission worker.

Until 2026-09-03 the decision table listed the word "Agent" as the trigger
for ``spawn_worker`` — the retired sub-agent system — so a person naming
their Gmail agent got a background worker, and "which agents do you have"
got an answer from a system that no longer exists. The prompt now has a
DELEGATE way that points at the team card and the two society tools.
"""

from __future__ import annotations

from jarvis.brain.router import SYSTEM_PROMPT


def test_delegate_is_a_way_of_its_own() -> None:
    assert "vier Wegen" in SYSTEM_PROMPT  # i18n-allow: prompt text under test
    assert "3. DELEGATE" in SYSTEM_PROMPT
    assert "4. SPAWN_WORKER" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.index("3. DELEGATE") < SYSTEM_PROMPT.index("4. SPAWN_WORKER")


def test_delegate_names_the_card_and_both_society_tools() -> None:
    assert "Your agent society" in SYSTEM_PROMPT
    assert "delegate_to_agent" in SYSTEM_PROMPT
    assert "society_status" in SYSTEM_PROMPT


def test_the_word_agent_no_longer_triggers_a_worker() -> None:
    spawn = SYSTEM_PROMPT[SYSTEM_PROMPT.index("4. SPAWN_WORKER") :]
    spawn = spawn[: spawn.index("BEI UNSICHERHEIT")]  # i18n-allow: prompt anchor
    assert '"Agent"' not in spawn
    assert "KEIN Agent der Karte" in spawn  # i18n-allow: prompt text under test
