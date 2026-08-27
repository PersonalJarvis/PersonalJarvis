"""Files dropped, pasted or picked into a chat composer.

Covers the whole path the composer uses: the attach route stores the bytes and
returns what was read from them, and the message that follows carries those
contents into the turn while the timeline still shows the person's own
sentence.

The vision layer is faked throughout. What is under test is the wiring — that a
chat can take a file at all, which is what it could not do — not whether a
model describes a picture well.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import attachments as chat_attachments
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agentic_ide import drop_analysis
from jarvis.ui.web.agent_chat_routes import router

# A one-pixel PNG — real bytes, so the store and the mime guess behave.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100" + "05fe02fe" + "0000000049454e44ae426082"
)


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = None
    app.state.agent_chat_factory = lambda: AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    return app


@pytest.fixture
def described(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every image comes back described, without a provider being involved."""

    async def fake_analyze(items, **_kwargs):  # noqa: ANN001, ANN202
        return [
            drop_analysis.DropAnalysis(
                name=item.name,
                reference=reference,
                kind="image",
                detail="A screenshot of a chat composer with no attach button.",
                described_by=drop_analysis.BY_VISION,
            )
            for item, reference in items
        ]

    monkeypatch.setattr(drop_analysis, "analyze", fake_analyze)


# ---------------------------------------------------------------- the route


def test_attach_stores_the_bytes_and_returns_what_was_read(tmp_path: Path, described) -> None:
    with TestClient(_app(tmp_path)) as client:
        res = client.post(
            "/api/agent-chat/attachments",
            files=[("files", ("shot.png", PNG, "image/png"))],
            data={"cwd": str(tmp_path), "surface": "jarvis"},
        )
        assert res.status_code == 200, res.text
        body = res.json()

    (row,) = body["attachments"]
    assert row["name"].endswith(".png")
    assert row["described_by"] == "vision"
    assert "composer" in row["detail"]
    # The copy landed in the chat's own folder, where the agent can open it.
    copies = list((tmp_path / ".jarvis" / "drops").glob("*.png"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == PNG


def test_attach_falls_back_to_the_surface_folder_when_the_cwd_is_gone(
    tmp_path: Path, described
) -> None:
    """A remembered folder that no longer exists must not refuse the file."""
    with TestClient(_app(tmp_path)) as client:
        res = client.post(
            "/api/agent-chat/attachments",
            files=[("files", ("shot.png", PNG, "image/png"))],
            data={"cwd": str(tmp_path / "moved-away"), "surface": "agent"},
        )
    assert res.status_code == 200, res.text
    assert res.json()["cwd"] == str(tmp_path)


def test_attach_refuses_an_empty_drop(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        res = client.post(
            "/api/agent-chat/attachments",
            data={"cwd": str(tmp_path), "paths": "   \n  "},
        )
    assert res.status_code == 422
    assert "nothing" in res.json()["detail"].lower()


def test_a_path_inside_the_folder_is_referenced_where_it_lies(
    tmp_path: Path, described
) -> None:
    """Nothing is copied for a file the agent can already open."""
    existing = tmp_path / "notes.png"
    existing.write_bytes(PNG)

    with TestClient(_app(tmp_path)) as client:
        res = client.post(
            "/api/agent-chat/attachments",
            data={"cwd": str(tmp_path), "paths": str(existing)},
        )
    assert res.status_code == 200, res.text
    (row,) = res.json()["attachments"]
    assert "notes.png" in row["reference"]
    assert not (tmp_path / ".jarvis" / "drops").exists()


# ------------------------------------------------------------- composition


def test_compose_leaves_an_ordinary_message_alone() -> None:
    assert chat_attachments.compose("what is this", []) == "what is this"
    assert chat_attachments.compose("  spaced  ", None) == "spaced"


def test_compose_carries_the_contents_into_the_message() -> None:
    item = drop_analysis.DropAnalysis(
        name="shot.png",
        reference='"shot.png"',
        kind="image",
        detail="A red error banner over the login form.",
        described_by=drop_analysis.BY_VISION,
    )
    out = chat_attachments.compose("fix this", [item])

    assert out.startswith("fix this")
    # The description is what makes the drop useful to a model that cannot open
    # the file, so it must be IN the message, not merely referenced.
    assert "A red error banner over the login form." in out
    assert "shot.png" in out


def test_to_analysis_survives_a_malformed_row() -> None:
    """The rows come back from a browser, so no shape may be assumed.

    A message somebody is waiting to send must not 500 over a stray field:
    anything that is not an object is dropped, and a row with a missing or
    mistyped field degrades to a named, empty-detail attachment.
    """
    rows = [
        {"name": "ok.png", "kind": "image", "detail": "d", "described_by": "vision"},
        {"kind": "image"},  # no name — kept under a placeholder, not dropped
        "not a dict",  # not an object at all — dropped
        {"name": "odd.txt", "detail": 42},  # wrong type — degrades to empty
    ]
    out = chat_attachments.to_analysis(rows)  # type: ignore[arg-type]
    assert [item.name for item in out] == ["ok.png", "file", "odd.txt"]
    assert out[2].detail == ""


# ------------------------------------------------------ the message it rides


def test_a_message_with_files_shows_the_sentence_and_sends_the_contents(
    tmp_path: Path,
) -> None:
    """The event log holds both halves, and each is used for its own job."""
    svc = AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    session = svc.store.create_session(
        provider="fakeprov", model="", effort="", cwd=str(tmp_path)
    )

    async def run() -> None:
        # No runner is reachable for "fakeprov" here, so the turn fails after
        # the user message is persisted — which is the part under test.
        await svc.send(
            session.session_id,
            "what is wrong here",
            [
                {
                    "name": "shot.png",
                    "reference": '"shot.png"',
                    "kind": "image",
                    "detail": "A red error banner.",
                    "described_by": "vision",
                    "note": "",
                }
            ],
        )
        await svc.cancel(session.session_id)

    asyncio.run(run())

    events = svc.store.list_events(session.session_id)
    user = next(e for e in events if e["kind"] == "user_message")
    payload = user["payload"]
    # What the person typed — the timeline shows this.
    assert payload["typed"] == "what is wrong here"
    # What the turn received — history rebuilds from this.
    assert "A red error banner." in payload["text"]
    assert payload["text"].startswith("what is wrong here")
    # The receipt the timeline draws under the message.
    assert payload["attachments"] == [
        {"name": "shot.png", "kind": "image", "described_by": "vision", "note": ""}
    ]


def test_files_alone_are_a_complete_message(tmp_path: Path) -> None:
    """Dropping a picture and pressing Enter says enough."""
    svc = AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    session = svc.store.create_session(
        provider="fakeprov", model="", effort="", cwd=str(tmp_path)
    )

    async def run() -> None:
        await svc.send(
            session.session_id,
            "",
            [{"name": "shot.png", "reference": "x", "kind": "image", "detail": "A chart."}],
        )
        await svc.cancel(session.session_id)

    asyncio.run(run())
    user = next(
        e for e in svc.store.list_events(session.session_id) if e["kind"] == "user_message"
    )
    assert "A chart." in user["payload"]["text"]


def test_an_empty_message_with_nothing_attached_is_still_refused(tmp_path: Path) -> None:
    svc = AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    session = svc.store.create_session(
        provider="fakeprov", model="", effort="", cwd=str(tmp_path)
    )

    async def run() -> None:
        with pytest.raises(ValueError):
            await svc.send(session.session_id, "   ", [])

    asyncio.run(run())
