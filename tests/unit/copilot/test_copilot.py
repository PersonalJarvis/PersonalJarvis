from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.copilot import material, workflow
from jarvis.copilot.gate import match_copilot_command

JA_ORDER = "この注文で10枚の資料を作って"  # 10-slide order
JA_WF_START = "作業の観察を開始して"
JA_WF_STOP = "作業の観察を終了"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (JA_ORDER, "material"),
        ("please make a slide deck about our Q3 results", "material"),
        (JA_WF_START, "workflow_start"),
        (JA_WF_STOP, "workflow_stop"),
        ("start observing my work", "workflow_start"),
        ("stop observing my work", "workflow_stop"),
    ],
)
def test_explicit_commands(text: str, kind: str) -> None:
    cmd = match_copilot_command(text)
    assert cmd is not None and cmd.kind == kind


@pytest.mark.parametrize("text", ["", "what is a slide deck?", "open excel", "hello"])
def test_ordinary_turns_reach_the_brain(text: str) -> None:
    assert match_copilot_command(text) is None


def _deck(n: int) -> material.Deck:
    raw = json.dumps(
        {
            "title": "Q3",
            "subtitle": "Report",
            "slides": [{"title": f"S{i}", "bullets": ["a", "b"]} for i in range(n)],
        }
    )
    return material.parse_outline("Sure:\n" + raw + "\nDone.")


def test_requested_count_and_quality(tmp_path: Path) -> None:
    assert material.requested_slide_count(JA_ORDER) == 10
    deck = _deck(9)
    pptx = tmp_path / "d.pptx"
    assert material.render_pptx(deck, pptx) is None
    checks = {c.name: c for c in material.quality_check(deck, JA_ORDER, pptx, None)}
    assert not checks["slide_count"].ok  # ordered 10, got 9
    assert checks["pptx_opens"].ok
    assert not checks["pdf_written"].ok
    assert checks["no_empty_slides"].ok


def test_crowded_and_long_slides_are_flagged() -> None:
    deck = material.Deck("T", slides=[{"title": "A", "bullets": ["x" * 200] + ["y"] * 7}])
    checks = {c.name: c for c in material.quality_check(deck, "make slides", None, None)}
    assert not checks["bullets_per_slide"].ok
    assert not checks["text_length"].ok


def test_no_json_is_an_error() -> None:
    with pytest.raises(ValueError):
        material.parse_outline("I cannot do that")


def test_html_escapes_content() -> None:
    deck = material.Deck("<b>x</b>", slides=[{"title": "t", "bullets": ["<script>"]}])
    assert "<script>" not in material.deck_html(deck)


class _Privacy:
    def is_allowed(self, *, window_title: str, process_name: str) -> tuple[bool, str]:
        return (process_name != "chrome.exe", "")


def test_observer_merges_samples_and_drops_private_titles() -> None:
    obs = workflow.Observer(_Privacy(), poll_s=3)
    obs.add("excel.exe", "a.xlsx", 0)
    obs.add("excel.exe", "a.xlsx", 3)
    obs.add("chrome.exe", "bank - secret", 6)
    assert len(obs.segments) == 2
    assert obs.segments[0].seconds == 3
    assert obs.segments[1].title == ""


def test_repeated_sequence_becomes_a_candidate() -> None:
    segs, t = [], 0.0
    for _ in range(3):
        for app in ("excel.exe", "powerpnt.exe", "outlook.exe"):
            segs.append(workflow.Segment(app, "", t, t + 60))
            t += 60
    analysis = workflow.analyse(segs)
    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.repeats >= 2 and "excel" in top.steps
    assert "nothing was automated" in workflow.report(analysis)


def test_no_repetition_no_candidate() -> None:
    segs = [workflow.Segment(a, "", i * 10, i * 10 + 5) for i, a in enumerate(["a", "b", "c"])]
    assert workflow.analyse(segs).candidates == []


def test_revision_replaces_only_that_page() -> None:
    deck = _deck(3)
    new = material.apply_revision(deck, 3, '{"title": "S1b", "bullets": ["short"]}')
    assert new.slides[1] == {"title": "S1b", "bullets": ["short"]}
    assert new.slides[0] == deck.slides[0] and new.slides[2] == deck.slides[2]
    title = material.apply_revision(deck, 1, '{"title": "New", "bullets": ["Sub"]}')
    assert title.title == "New" and title.subtitle == "Sub"


def test_build_then_load_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(material, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(material, "render_pdf", lambda deck, path: "no browser in test")
    folder = material.output_dir("x")
    material.build(_deck(2), "make 2 slides", folder)
    latest = material.load_latest()
    assert latest is not None
    deck, order, where = latest
    assert order == "make 2 slides" and where == folder and len(deck.slides) == 2


def test_draft_is_marked_unreviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow, "user_data_dir", lambda: tmp_path)
    analysis = workflow.analyse(
        [
            workflow.Segment(a, "", i * 60, i * 60 + 50)
            for i, a in enumerate(["excel", "outlook"] * 3)
        ]
    )
    workflow.save_candidates(analysis)
    assert workflow.load_candidates()[0].steps == analysis.candidates[0].steps
    path = workflow.save_draft(1, "```powershell\nWrite-Output 1\n```")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# DRAFT") and "NOT run" in text and "Write-Output 1" in text
    assert "```" not in text


def test_risky_lines_are_commented_out() -> None:
    script = "$m.Send()\nRemove-Item C:/x -Recurse\nWrite-Output ok\n# $m.Send()"
    safe, blocked = workflow.neutralise(script)
    assert blocked == 2
    lines = safe.splitlines()
    assert lines[0].startswith("# BLOCKED") and lines[1].startswith("# BLOCKED")
    assert lines[2] == "Write-Output ok" and lines[3] == "# $m.Send()"
