from pathlib import Path

from jarvis.society.result_links import reviewable_outputs


def test_only_existing_workspace_files_are_verified(tmp_path: Path):
    workspace = tmp_path / "agent"
    workspace.mkdir()
    report = workspace / "report.md"
    report.write_text("Result", encoding="utf-8")
    outside = tmp_path / "private.md"
    outside.write_text("Private", encoding="utf-8")

    output, evidence = reviewable_outputs(
        ["report.md", "missing.md", str(outside), "https://example.com/source", "file:///tmp/no"],
        str(workspace),
    )

    assert output == [str(report.resolve()), "https://example.com/source"]
    assert evidence == [str(report.resolve())]
