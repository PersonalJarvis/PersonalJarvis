"""Tests for the CI gate that keeps other products out of our design story.

The gate lives in scripts/ci/ (not an importable package), so that directory
goes on sys.path first, the way CI runs the script directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CI = Path(__file__).resolve().parents[3] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

import check_design_references as gate  # noqa: E402


@pytest.mark.parametrize(
    "line",
    [
        "Give the area picker ShareX's region-capture look",
        "The editor follows CleanShot X's annotate tool",
        "Draw traces the way the Codex app does",
        "A quiet, Claude-style front page: one greeting, one composer.",
        "// Rows read like the Claude app's column: regular weight",
        "* Left, top to bottom (maintainer, modelled on Grok Bot's New Bot dialog)",
        "How it works (the idea is borrowed from Hermes Agent's per-file runner):",
        "Rebuilt after a pipeline inspired by Hermes Agent",
        "The quick switcher is a Spotlight-style launcher.",
        "# --- the Codex-like look: filled discs, a glossy orb",
        "Approval rules — Grok-style, require wins.",
    ],
)
def test_design_references_are_flagged(line: str) -> None:
    assert gate.line_hits(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Import MCP servers from the Claude Desktop config.",
        "clients (Claude Desktop, Cursor, VS Code, Codex) launch a command",
        "Parse a Codex-style `apply_patch` payload.",
        "Synthesize a tool_use Claude-style event so the timeline renders it.",
        "Modelled on `agentAccountsApi.ts` (same send<T> shape).",
        "a linear fade modeled on the cursor position along an arc",
        "Ask for something big, and Jarvis sends background agents like Claude Code.",
        "The Codex CLI worker reads the same profile as the Claude worker.",
        "Spotlight search reads the per-volume metadata store kept by mds.",
        "adapted from Nous Research's avatar.tsx design-ref-allow",
    ],
)
def test_integration_and_neutral_lines_pass(line: str) -> None:
    assert gate.line_hits(line) == [], line


def test_third_party_files_are_skipped() -> None:
    lines = [
        ("CODE_OF_CONDUCT.md", 1, "inspired by Mozilla's code of conduct, see ShareX"),
        ("jarvis/ui/web/frontend/public/THIRD_PARTY_NOTICES.txt", 2, "ShareX"),
        ("docs/appshots.md", 3, "The picker copies ShareX."),
        ("assets/brand/logo.png", 4, "ShareX"),
    ]
    assert [v[0] for v in gate.find_violations(lines)] == ["docs/appshots.md"]


def test_added_lines_from_a_staged_diff_are_scanned() -> None:
    diff = (
        "diff --git a/docs/x.md b/docs/x.md\n"
        "--- a/docs/x.md\n"
        "+++ b/docs/x.md\n"
        "@@ -1,0 +4,2 @@\n"
        "+The card stacks as in CleanShot X.\n"
        "+The card stacks upward.\n"
    )
    violations = gate.find_violations(gate.parse_added_lines(diff))
    assert [(v[0], v[1]) for v in violations] == [("docs/x.md", 4)]
