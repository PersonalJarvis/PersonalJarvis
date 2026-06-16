"""Unit tests for server-side artifact HTML rendering (markdown + escape + CSP)."""
from __future__ import annotations

import builtins

from jarvis.ui.web.artifact_view import VIEW_CSP, render_artifact_html


def test_markdown_renders_heading_to_html():
    out = render_artifact_html("report.md", "# Title\n\nHello")
    assert "<h1>Title</h1>" in out
    assert "Hello" in out
    assert "<!doctype html>" in out.lower()


def test_markdown_table_renders():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = render_artifact_html("t.md", md)
    assert "<table>" in out


def test_non_markdown_is_escaped_pre():
    out = render_artifact_html("data.txt", "<script>alert(1)</script>")
    assert "<pre>" in out
    assert "&lt;script&gt;" in out
    assert "<script>alert(1)</script>" not in out


def test_markdown_missing_lib_falls_back_to_pre(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "markdown":
            raise ImportError("no markdown")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = render_artifact_html("report.md", "# Title")
    assert "<pre>" in out
    assert "# Title" in out  # raw, not rendered to <h1>


def test_csp_blocks_scripts():
    assert "default-src 'none'" in VIEW_CSP
    assert "script-src" not in VIEW_CSP
