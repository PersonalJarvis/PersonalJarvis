"""Local-outcome gate: natural file/folder/system requests must mandate
``run_shell`` (shell-consistency rework 2026-08-08) — and conversational /
how-to / external-integration turns must NOT.

The German utterances below are simulated voice inputs, i.e. the speech
input the gate classifies — content under test (same rationale as
``test_local_action_gate.py``; registered in scripts/ci/german-allowlist.txt).
"""

from __future__ import annotations

import pytest

from jarvis.brain.local_outcome_gate import (
    RUN_SHELL_OUTCOME_DIRECTIVE,
    resolve_local_outcome_mandate,
)

# The maintainer's canonical failing request (2026-08-08) leads the list.
MANDATE_UTTERANCES = [
    "Erstell einen neuen Ordner namens Personal Jarvis auf meinem Desktop",
    "Kannst du bitte einen neuen Ordner auf dem Desktop erstellen?",
    "Hey Jarvis, leg mal ein Verzeichnis für Rechnungen an",
    "Lösch die Datei alt.txt aus dem Downloads-Ordner",
    "Benenn die Datei Bericht.docx in Final.docx um",
    "Verschieb die Zip auf den Desktop",
    "Kopier die Datei bitte nach D:",
    "Entpack das Archiv im Downloads-Ordner",
    "Zeig mir, was in meinem Downloads-Ordner liegt",
    "Lies mir die Datei notes.txt vor",
    "Führ das Skript build.ps1 aus",
    "create a folder called projects on my desktop",
    "delete the old files in the downloads folder",
    "rename the file notes.txt to todo.txt",
    "unzip the archive on my desktop",
    "run the cleanup script",
]

NO_MANDATE_UTTERANCES = [
    # Smalltalk / conversation — no verb+object pair.
    "Hallo, wie geht's dir heute?",
    "Danke dir, das war super",
    # Definition / how-to — answered inline, never acted on.
    "Was ist eine Datei?",
    "Wie erstelle ich einen Ordner in Windows?",
    "How do I create a folder on Windows?",
    # External dispatch — the integration machinery owns it.
    "Schick die Datei bitte per Mail an Harald",
    # Cloud storage — not the local disk.
    "Lad die Datei in mein Google Drive hoch",
    # Explicit GUI vehicle — computer-use owns the screen.
    "Klick auf den Ordner auf dem Desktop",
    # Explicit heavy vehicle — AD-S9, spawn owns it.
    "Spawn einen Subagenten, der den Ordner aufräumt",
    # Other domains: verb matches, but no file-system object.
    "Lösch den Termin am Freitag",
    "Erstell einen Kalendereintrag für Montag",
    # Below the minimum command length / empty.
    "ordne",
    "",
]


@pytest.mark.parametrize("utterance", MANDATE_UTTERANCES)
def test_local_outcome_mandates_run_shell(utterance: str) -> None:
    mandate = resolve_local_outcome_mandate(utterance)
    assert mandate is not None, f"expected a run_shell mandate for: {utterance!r}"
    tool, directive = mandate
    assert tool == "run_shell"
    assert directive == RUN_SHELL_OUTCOME_DIRECTIVE


@pytest.mark.parametrize("utterance", NO_MANDATE_UTTERANCES)
def test_conversational_and_foreign_turns_do_not_mandate(utterance: str) -> None:
    assert resolve_local_outcome_mandate(utterance) is None, (
        f"expected NO mandate for: {utterance!r}"
    )


def test_directive_names_the_tool_and_forbids_the_refusal() -> None:
    # The directive is the LLM-facing contract: it must name run_shell and
    # explicitly outlaw the "no tool" refusal the live bug produced.
    assert "run_shell" in RUN_SHELL_OUTCOME_DIRECTIVE
    assert "NEVER claim you lack a tool" in RUN_SHELL_OUTCOME_DIRECTIVE
