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
    RUN_SHELL_TIMER_DIRECTIVE,
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
    # Foreign-domain noun alongside a generic FS noun (code-review finding
    # 2026-08-08): a mail FOLDER is a mailbox operation, not a disk folder.
    "Verschieb die Mail in einen anderen Ordner",
    "move the email to a different folder",
    "Lösch die WhatsApp-Nachrichten in dem Ordner",
    "Kopier den Termin in den Kalender-Ordner",
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


# Timer class (2026-08-08 follow-up): "stell einen Timer" was the maintainer's
# original positive surprise — and the next day it was refused again.
TIMER_UTTERANCES = [
    "Stell einen Timer auf 10 Minuten",
    "Setz mir bitte einen Timer für 5 Minuten",
    "Kannst du einen Timer auf 20 Minuten stellen?",
    "Timer 10 Minuten",
    "Mach mir einen Wecker für 7 Uhr",
    "Stopp den Timer bitte",
    "set a timer for 15 minutes",
    "start a 5 minute countdown",
]

TIMER_NO_MANDATE = [
    "Was ist ein Timer?",              # definition
    "Wie funktioniert ein Timer?",     # no set/stop verb, no duration
    "Der Timer gestern war praktisch", # remark, no verb/duration
]


@pytest.mark.parametrize("utterance", MANDATE_UTTERANCES)
def test_local_outcome_mandates_run_shell(utterance: str) -> None:
    mandate = resolve_local_outcome_mandate(utterance)
    assert mandate is not None, f"expected a run_shell mandate for: {utterance!r}"
    tool, directive = mandate
    assert tool == "run_shell"
    assert directive == RUN_SHELL_OUTCOME_DIRECTIVE


@pytest.mark.parametrize("utterance", TIMER_UTTERANCES)
def test_timer_requests_mandate_run_shell_with_detach_directive(utterance: str) -> None:
    mandate = resolve_local_outcome_mandate(utterance)
    assert mandate is not None, f"expected a timer mandate for: {utterance!r}"
    tool, directive = mandate
    assert tool == "run_shell"
    assert directive == RUN_SHELL_TIMER_DIRECTIVE


@pytest.mark.parametrize("utterance", TIMER_NO_MANDATE)
def test_timer_questions_and_remarks_do_not_mandate(utterance: str) -> None:
    assert resolve_local_outcome_mandate(utterance) is None, (
        f"expected NO mandate for: {utterance!r}"
    )


def test_timer_directive_forces_detached_process() -> None:
    # The tool call times out after ~30s — a blocking sleep would fail every
    # timer, so the directive must demand a detached background process.
    assert "DETACHED" in RUN_SHELL_TIMER_DIRECTIVE
    assert "run_shell" in RUN_SHELL_TIMER_DIRECTIVE


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


FICTIONAL_DRAFT_ASSIGNMENT = (
    "[assignment from the user]\n"
    "Prepare a communication draft for review. Do not send or publish it.\n\n"
    "Draft a 450-word internal update for an imaginary Mars research team about "
    "preparing a communications outpost for its first test. Include a subject, "
    "purpose, five practical checks, risks still to verify, and a clear next action. "
    "This is a fictional draft for review only. Do not send, publish, browse, "
    "run commands, or change files.\n"
    "When you are done, end with a handoff: what is done, where the output is, "
    "what evidence you used, what remains open, who should own the next step."
)


@pytest.mark.parametrize(
    "utterance",
    [
        FICTIONAL_DRAFT_ASSIGNMENT,
        "Do not run commands or change files. When done explain what remains open.",
        "Write a fictional report. Don't send, publish, run commands, or change files.",
        "Write a short report without running shell commands or changing files.",
        "Do not create a folder, copy files, or run a script.",
        "Never run commands or create files.",
        "Do not run `python script.py`.",
        "Explain `python script.py` without running it.",
        "The subject is files. List the remaining questions.",
        "Erstelle keinen Ordner und kopiere keine Dateien.",  # i18n-allow
        "Die Datei nicht loeschen.",  # i18n-allow
        "Loesch die Datei nicht.",  # i18n-allow
        "How do I create a folder? Show the commands, do not run them.",
        "Use the mouse. Create a folder named drafts on my desktop.",
        "How do I create a folder? Please list the commands and explain them.",
    ],
)
def test_prohibited_actions_and_unrelated_clauses_do_not_mandate(utterance: str) -> None:
    assert resolve_local_outcome_mandate(utterance) is None


@pytest.mark.parametrize(
    "utterance",
    [
        "Create a folder named drafts on my desktop. Do not publish it.",
        "Do not delete the file; copy it to a backup folder instead.",
        "Do not delete files, but create a folder named backup.",
        "Create a folder and do not delete existing files.",
        "Create a folder without deleting existing files.",
        "Do not send an email. Create a folder named drafts on the desktop.",
        "Do not rename the file. Copy the file notes.txt to a backup folder.",
        "Loesch die Datei nicht; kopier die Datei in einen anderen Ordner.",  # i18n-allow
        "List all files not ending in .tmp.",
        'Create a folder named "not needed" on my desktop.',
        "Create a folder named 'not needed' on my desktop.",
        "Create a folder named `do not delete` on my desktop.",
        "Do not delete anything, and create a folder named backup.",
        "How do I create a folder? Now actually create a folder named drafts.",
        "Do not use the mouse. Create a folder named drafts on my desktop.",
        "Run `python script.py`",
        'Execute "python script.py"',
    ],
)
def test_positive_actions_survive_a_separate_prohibition(utterance: str) -> None:
    assert resolve_local_outcome_mandate(utterance) == ("run_shell", RUN_SHELL_OUTCOME_DIRECTIVE)
