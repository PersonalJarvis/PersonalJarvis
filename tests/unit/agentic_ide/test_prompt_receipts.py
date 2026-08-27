"""A pane's chat shows what the person said and dropped, not the brief.

The complaint (maintainer, 2026-08-27): the user's turn in a pane's chat read
"## Task … ## Dropped files ### shot.png - '.jarvis/drops/…' Could not be
described" — the brief Jarvis typed into the CLI, drawn as if the person had
written it, with the picture reduced to a path. The receipt recorded beside
the prompt at send time is what puts the sentence and the image back.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import agent_transcript, drops, prompt_history, prompt_receipts
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.drop_analysis import DropAnalysis
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

# A 1x1 PNG — enough for the file route to serve something a browser draws.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc000000301010018dd8db00000000049454e44ae426082"
)


def _shot(reference: str = '".jarvis/drops/20260827-shot.png"') -> DropAnalysis:
    return DropAnalysis(
        name="shot.png",
        reference=reference,
        kind="image",
        detail="",
        described_by="none",
        note="Could not be described (ClientError); it is attached as a file.",
    )


BRIEF = (
    "## Task\nScroll the reasoning traces into view automatically.\n\n"
    "## Dropped files\n### shot.png - \".jarvis/drops/20260827-shot.png\"\n"
    "Could not be described (ClientError); it is attached as a file."
)
SAID = "Ich möchte, dass im Agentic IDE automatisch mit runtergescrollt wird."  # i18n-allow


def _entry(text: str, typed: str = "", attachments: tuple[dict, ...] = (), at: float = 1.0):
    return prompt_history.PromptHistoryEntry(
        id=f"e{at}",
        sequence=int(at),
        text=text,
        at=at,
        submitted=True,
        typed=typed,
        attachments=attachments,
    )


def _user(text: str, ts: int = 1000) -> dict:
    return {"seq": ts, "ts_ms": ts, "kind": "user_message", "payload": {"text": text}}


# ------------------------------------------------------------------ pieces


@pytest.mark.parametrize(
    ("reference", "path"),
    [
        ("@.jarvis/drops/a.png", ".jarvis/drops/a.png"),
        ('".jarvis/drops/with space.png"', ".jarvis/drops/with space.png"),
        ("'docs/x.md'", "docs/x.md"),
        ("", ""),
        ("plain/path.png", "plain/path.png"),
    ],
)
def test_dereference_undoes_both_reference_shapes(reference: str, path: str) -> None:
    assert drops.dereference(reference) == path


def test_dereference_is_the_inverse_of_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.workspace import agents as workspace_agents

    for wants_at in (True, False):
        spec = SimpleNamespace(file_reference="at" if wants_at else "quoted")
        monkeypatch.setattr(workspace_agents, "get_agent", lambda _n, s=spec: s)
        for path in (".jarvis/drops/a.png", ".jarvis/drops/with space.png"):
            assert drops.dereference(drops.reference(path, agent="x")) == path


def test_receipts_keep_the_viewer_facts_and_drop_the_description() -> None:
    rows = prompt_receipts.receipts_for([_shot(), SimpleNamespace(name="", reference="@x")])
    assert rows == (
        {
            "name": "shot.png",
            "kind": "image",
            "described_by": "none",
            "note": "Could not be described (ClientError); it is attached as a file.",
            "path": ".jarvis/drops/20260827-shot.png",
        },
    )


def test_file_url_matches_the_frontend_twin() -> None:
    # `workspaceFileUrl` in agenticIdeApi.ts writes the same address.
    assert (
        prompt_receipts.file_url("ide_1", ".jarvis/drops/a b.png")
        == "/api/agentic-ide/workspaces/ide_1/file?path=.jarvis%2Fdrops%2Fa%20b.png"
    )


def test_history_row_round_trips_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prompt_history, "_store_dir", lambda: tmp_path)
    entry = _entry(BRIEF, typed=SAID, attachments=prompt_receipts.receipts_for([_shot()]))
    prompt_history.append("pane", entry)
    assert prompt_history.load("pane") == [entry]


def test_a_row_written_before_receipts_existed_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prompt_history, "_store_dir", lambda: tmp_path)
    old_row = {"id": "old", "sequence": 1, "text": "Continue.", "at": 1.0, "submitted": True}
    target = prompt_history._path("pane")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
    [loaded] = prompt_history.load("pane")
    assert (loaded.typed, loaded.attachments) == ("", ())


# ---------------------------------------------------------------- annotate


def test_the_users_turn_becomes_their_sentence_and_their_picture() -> None:
    entries = [_entry(BRIEF, typed=SAID, attachments=prompt_receipts.receipts_for([_shot()]))]
    [event] = prompt_receipts.annotate([_user(BRIEF)], entries, workspace_id="ide_1")
    assert event["payload"]["typed"] == SAID
    assert event["payload"]["text"] == BRIEF
    assert event["payload"]["attachments"] == [
        {
            "name": "shot.png",
            "kind": "image",
            "described_by": "none",
            "note": "Could not be described (ClientError); it is attached as a file.",
            "url": (
                "/api/agentic-ide/workspaces/ide_1/file"
                "?path=.jarvis%2Fdrops%2F20260827-shot.png"
            ),
        }
    ]


def test_whitespace_and_the_readers_middle_cut_do_not_lose_the_match() -> None:
    long_brief = "## Task\n" + ("Inspect the parser carefully. " * 400)
    entries = [_entry(long_brief, typed=SAID)]
    shown = agent_transcript._clip(long_brief.replace("\n", " ") + " ")
    assert prompt_receipts.CLIP_MARK in shown
    [event] = prompt_receipts.annotate([_user(shown)], entries, workspace_id="ide_1")
    assert event["payload"]["typed"] == SAID


def test_a_prompt_typed_straight_into_the_cli_is_left_alone() -> None:
    entries = [_entry(BRIEF, typed=SAID)]
    events = [
        _user("fix the tests"),
        {"seq": 2, "ts_ms": 2, "kind": "assistant_text", "payload": {"text": "ok"}},
    ]
    assert prompt_receipts.annotate(events, entries, workspace_id="ide_1") == events


def test_two_identical_briefs_each_take_their_own_receipt() -> None:
    entries = [
        _entry(BRIEF, typed="first", at=1.0),
        _entry(BRIEF, typed="second", at=2.0),
    ]
    first, second, third = prompt_receipts.annotate(
        [_user(BRIEF, 1), _user(BRIEF, 2), _user(BRIEF, 3)], entries, workspace_id="w"
    )
    assert [e["payload"]["typed"] for e in (first, second, third)] == ["first", "second", "first"]


def test_a_text_file_gets_no_url_and_nothing_is_mutated() -> None:
    doc = DropAnalysis(
        name="notes.md", reference="@notes.md", kind="text", detail="x", described_by="extraction"
    )
    entries = [_entry(BRIEF, attachments=prompt_receipts.receipts_for([doc]))]
    original = _user(BRIEF)
    [event] = prompt_receipts.annotate([original], entries, workspace_id="w")
    assert "url" not in event["payload"]["attachments"][0]
    assert "typed" not in event["payload"]
    assert original["payload"] == {"text": BRIEF}


# ------------------------------------------------------------- end to end


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(prompt_history, "_store_dir", lambda: tmp_path / "prompt-history")
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    from jarvis.ui.web import agentic_ide_routes

    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def client(registry: Registry) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live_pane(registry: Registry, folder: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    await registry.start(str(folder), [{"agent": "claude"}])
    assert registry.session is not None
    term = registry.session.terminals[0]
    await registry.attach(term.name, 100, 30, _noop, _noop_exit)

    async def accepted(*_args: object) -> bool:
        return True

    monkeypatch.setattr(registry, "_write_and_confirm", accepted)
    term.resume = ResumeHandle(kind="claude_session", id="sess-1", captured_at=1.0)
    return term.name


async def test_the_timeline_serves_the_sentence_and_a_picture_that_loads(
    client: TestClient, registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    name = await _live_pane(registry, workspace, monkeypatch)
    [stored] = drops.store(workspace, [("shot.png", PNG)])
    shot = _shot(reference=drops.reference(stored.relative_path, agent="claude"))

    # The typed path: the pane chat's composer sends the sentence with the file.
    await registry.send_prompt(name, BRIEF, typed=SAID, attachments=[shot])

    # What the CLI wrote down is the brief, whitespace and all.
    monkeypatch.setattr(
        agent_transcript,
        "read_timeline",
        lambda *_a, **_k: agent_transcript.TimelineRead(events=[_user(BRIEF + " ")]),
    )
    body = client.get(f"/api/agentic-ide/terminals/{name}/timeline").json()
    assert body["available"] is True
    [event] = body["events"]
    assert event["payload"]["typed"] == SAID
    [receipt] = event["payload"]["attachments"]
    assert receipt["name"] == "shot.png"
    assert receipt["url"].startswith("/api/agentic-ide/workspaces/")

    picture = client.get(receipt["url"])
    assert picture.status_code == 200
    assert picture.content == PNG
    assert picture.headers["content-type"].startswith("image/png")


async def test_a_verbatim_prompt_records_no_typed_copy(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = await _live_pane(registry, tmp_path, monkeypatch)
    await registry.send_prompt(name, "fix the tests", typed="fix the tests")
    assert registry.session is not None
    [record] = registry.session.terminals[0].prompt_records
    assert (record.typed, record.attachments) == ("", ())
