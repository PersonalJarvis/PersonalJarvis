"""Integration tests for /api/soul — the assistant's profile page.

Real files under ``tmp_path``: SOUL.md in the data dir, the instructions file
next to it, and the notebooks in a throwaway vault. No provider, no mocks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import jarvis.core.config as core_config
import jarvis.society.memory as society_memory
from jarvis.brain import agent_instructions, identity
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.memory.learning import notebook as notebook_module
from jarvis.memory.learning.notebook import JarvisNotebook
from jarvis.memory.soul import Soul
from jarvis.memory.templates import render_soul_md
from jarvis.ui.web.server import WebServer


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(society_memory, "resolve_society_vault", lambda cfg: tmp_path / "vault")
    notebook_module.set_active(None)
    identity.invalidate_cache()
    yield
    notebook_module.set_active(None)


@pytest.fixture
def server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.config = cfg
    s.app.state.bus = bus
    return s


def _write_soul(text: str | None = None) -> Path:
    path = identity.soul_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else render_soul_md(), encoding="utf-8")
    return path


def _files(body: dict) -> dict[str, dict]:
    return {f["id"]: f for f in body["files"]}


def test_a_fresh_install_lists_every_file_as_missing_and_creates_none(
    server: WebServer, tmp_path: Path
) -> None:
    with TestClient(server.app) as client:
        body = client.get("/api/soul").json()
    files = _files(body)
    assert list(files) == ["soul", "instructions", "memory", "user"]
    assert all(not f["exists"] for f in files.values())
    assert files["soul"]["filename"] == "SOUL.md"
    assert files["instructions"]["filename"] == agent_instructions.instructions_filename(
        server.app.state.config
    )
    assert files["instructions"]["template"].strip()
    assert body["activity"] == []
    assert body["learning"] is False
    # A GET never seeds anything.
    assert not (tmp_path / "vault").exists()
    assert not identity.soul_path().exists()


def test_the_character_is_split_into_labelled_rows(server: WebServer) -> None:
    _write_soul()
    with TestClient(server.app) as client:
        soul = _files(client.get("/api/soul").json())["soul"]
    assert soul["exists"] is True and soul["editable"] is True
    who = {row["label"]: row["text"] for row in soul["character"]["who"]}
    assert "Role" in who and "Vibe" in who
    # The name line is managed by the wake word, so it is not a character row.
    assert "Name" not in who
    assert soul["character"]["tone"] and soul["character"]["limits"]
    assert "## Who I am" in soul["content"]
    assert not soul["content"].startswith("---")  # the frontmatter stays out of the editor


def test_saving_soul_md_keeps_the_frontmatter_and_the_learned_notes(server: WebServer) -> None:
    path = _write_soul()
    soul = Soul.load(path)
    soul.append_calibration("Answers in short spoken sentences.")
    soul.save()
    edited = Soul.load(path).body.replace(
        "Helpful but not obsequious.", "Calm, warm and to the point."
    )
    with TestClient(server.app) as client:
        resp = client.put("/api/soul/file", json={"content": edited})
        assert resp.status_code == 200
        saved = _files(resp.json())["soul"]
    assert "Calm, warm and to the point." in saved["content"]
    assert [e["text"] for e in saved["learned"]] == ["Answers in short spoken sentences."]
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---") and "subject_type: agent" in raw
    # The prompt sees the edit on its next build.
    identity.invalidate_cache()
    assert "Calm, warm and to the point." in identity.character_block(path=path)


def test_saving_refuses_an_empty_or_missing_soul(server: WebServer) -> None:
    with TestClient(server.app) as client:
        assert client.put("/api/soul/file", json={"content": "x"}).status_code == 404
        _write_soul()
        assert client.put("/api/soul/file", json={"content": "   "}).status_code == 400


def test_notebooks_and_activity_come_from_the_vault(server: WebServer, tmp_path: Path) -> None:
    book = JarvisNotebook(tmp_path / "vault")
    book.keep_explicit("Ruben's sister is called Lena.", via_tool=True, evidence="merk dir das")
    book.apply(target="user", operation="add", text="The user prefers short answers.")
    with TestClient(server.app) as client:
        body = client.get("/api/soul").json()
    files = _files(body)
    memory = files["memory"]["entries"]
    assert len(memory) == 1 and memory[0]["explicit"] is True
    assert "Lena" in memory[0]["text"]
    assert [e["text"] for e in files["user"]["entries"]] == ["The user prefers short answers."]
    assert files["memory"]["editable"] is False
    # Newest first, with what the person said.
    assert [a["target"] for a in body["activity"]] == ["user", "memory"]
    assert body["activity"][1]["evidence"] == "merk dir das"


def test_forgetting_a_note_needs_the_running_loop_and_is_ledgered(
    server: WebServer, tmp_path: Path
) -> None:
    book = JarvisNotebook(tmp_path / "vault")
    book.apply(target="memory", operation="add", text="Builds Personal Jarvis.")
    entry_id = book.entries()["memory"][0].id
    with TestClient(server.app) as client:
        assert client.delete(f"/api/soul/entries/memory/{entry_id}").status_code == 503
        notebook_module.set_active(book)
        assert client.delete("/api/soul/entries/memory/nope").status_code == 404
        assert client.delete(f"/api/soul/entries/bogus/{entry_id}").status_code == 422
        resp = client.delete(f"/api/soul/entries/memory/{entry_id}")
        assert resp.status_code == 200
        assert _files(resp.json())["memory"]["entries"] == []
        assert resp.json()["activity"][0]["operation"] == "remove"
        assert resp.json()["activity"][0]["text"] == "Builds Personal Jarvis."
    ledger = (book.folder / notebook_module.LEDGER_NAME).read_text(encoding="utf-8")
    last = json.loads(ledger.strip().splitlines()[-1])
    assert last["source"] == "soul page: forgotten by the user"
