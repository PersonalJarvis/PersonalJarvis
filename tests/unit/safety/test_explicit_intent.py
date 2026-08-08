"""Explicit-intent vocabulary: the user's own words authorize the destruction.

Claude-Code permission model (mandate 2026-08-08): "lösch den Ordner Urlaub"
must not be answered with "do you really want me to delete?". The check is a
deterministic de/en/es verb-stem match — no LLM, no entity matching.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.tool.run_shell import RunShellTool
from jarvis.safety.explicit_intent import utterance_confirms_destruction


@pytest.mark.parametrize(
    "utterance",
    [
        "Lösch den Ordner Urlaub vom Desktop.",
        "Bitte lösche die alte Datei",
        "kannst du das entfernen",
        "wirf das in den Papierkorb",
        "formatier den Stick",
        "fahr den Rechner runterfahren",  # STT word salad still carries the stem
        "delete the old build folder",
        "please remove that file",
        "wipe the temp directory",
        "shut down the computer",
        "borra la carpeta vieja",
        "elimina ese archivo",
        "apaga el ordenador",
    ],
)
def test_destruction_verbs_confirm(utterance: str) -> None:
    assert utterance_confirms_destruction(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "   ",
        "Leg einen Ordner Urlaub auf dem Desktop an.",
        "Was liegt auf meinem Desktop?",
        "Räum die Dateien in Unterordner",  # organizing is NOT deleting
        "create a folder called delete-me-later"
        .replace("delete-me-later", "archive"),  # no destruction verb at all
        "Stelle einen Timer auf acht Minuten.",
    ],
)
def test_harmless_utterances_do_not_confirm(utterance: str) -> None:
    assert utterance_confirms_destruction(utterance) is False


class TestRunShellIntentHook:
    def test_destructive_command_with_spoken_delete_confirms(self) -> None:
        tool = RunShellTool()
        assert tool.intent_confirms_args(
            {"command": "Remove-Item -Recurse C:\\Users\\x\\Desktop\\Urlaub"},
            "Lösch den Ordner Urlaub vom Desktop.",
        ) is True

    def test_brain_initiated_deletion_does_not_confirm(self) -> None:
        # The utterance never mentioned deleting — the brain chose rm on its
        # own as a means to an end. The confirmation must stay.
        tool = RunShellTool()
        assert tool.intent_confirms_args(
            {"command": "rm -rf ./build"},
            "mach das Projekt startklar",
        ) is False

    def test_non_destructive_command_never_needs_the_shortcut(self) -> None:
        tool = RunShellTool()
        assert tool.intent_confirms_args(
            {"command": "ls -la"},
            "lösch nachher mal was",
        ) is False

    def test_empty_utterance_never_confirms(self) -> None:
        tool = RunShellTool()
        assert tool.intent_confirms_args(
            {"command": "rm -rf ./build"}, "",
        ) is False
