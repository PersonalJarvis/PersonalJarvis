"""Dropping a file on the prompt bar: stored, read, and folded into the prompt.

The route has to serve two different gestures without them interfering:

* **A drop on a PANE** — types a path and stops. Cheap, no model, unchanged.
* **A drop on the PROMPT BAR** — reads the file (describes an image, extracts a
  document), types nothing, and hands the contents back so the composed prompt
  can carry them. That is what makes dropping a screenshot work against a coding
  agent that cannot open one.

The failure this guards is quiet, which is why it is pinned end to end: an
analysis that is produced and then dropped on the floor looks exactly like one
that was never asked for, and the user only finds out when the agent asks what
the screenshot showed.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import drop_analysis, drops, prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.drop_analysis import DropAnalysis
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _staged_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that can see, so the description is deterministic.

    Staged at the resolver rather than the call: that is the seam a real install
    varies at, and it lets one test remove vision entirely to check the honest
    degradation (§3).
    """

    class _Delta:
        content = "A login dialog; the submit button overflows and reads 'Sign inn'."

    class _Seeing:
        supports_vision = True

        async def complete(self, request):  # noqa: ANN001, ANN202
            yield _Delta()

    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", _Seeing)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
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


async def _workspace(registry: Registry, folder: Path) -> str:
    await registry.start(str(folder), [{"agent": "claude"}])
    assert registry.session is not None
    term = registry.session.terminals[0]
    await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return term.name


def _upload(name: str = "shot.png", data: bytes = PNG) -> dict:
    return {"files": (name, io.BytesIO(data), "image/png")}


# ------------------------------------------------------------------- analysis
async def test_a_dropped_screenshot_comes_back_described(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["analysis"]) == 1
    entry = body["analysis"][0]
    assert entry["kind"] == "image"
    assert entry["described_by"] == "vision"
    assert "submit button overflows" in entry["detail"]
    # The reference has to point at the file that was actually written, so an
    # agent that CAN open it is not sent to a path that does not exist.
    assert drops.DROP_DIRNAME.split("/")[0] in entry["reference"]


async def test_deliver_false_types_nothing_into_the_pane(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)
    written: list[str] = []
    term = registry.session.terminals[0]
    registry.write = lambda key, text: written.append(text) or True  # type: ignore[assignment]

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    assert response.json()["delivered"] is False
    assert written == []
    # Held, not lost: the file is on disk where the prompt will point at it.
    assert (tmp_path / drops.DROP_DIRNAME).is_dir()
    assert list((tmp_path / drops.DROP_DIRNAME).iterdir())
    assert term is not None


async def test_voice_orb_drop_is_staged_on_the_selected_pane(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"stage_for_voice": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["staged_for_voice"] == 1
    assert body["voice_batch_id"]
    assert registry.session is not None
    term = registry.session.find(name)
    assert term is not None
    assert len(term.pending_prompt_attachment_batches) == 1
    batch = term.pending_prompt_attachment_batches[0]
    assert batch.batch_id == body["voice_batch_id"]
    assert isinstance(batch.attachments[0], DropAnalysis)
    assert "submit button overflows" in batch.attachments[0].detail


async def test_pending_voice_drop_can_be_listed_and_removed(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)
    staged = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"stage_for_voice": "true", "deliver": "false"},
    ).json()

    pending = client.get(
        f"/api/agentic-ide/terminals/{name}/voice-attachments"
    )
    assert pending.status_code == 200
    assert pending.json()["batches"] == [
        {
            "batch_id": staged["voice_batch_id"],
            "files": ["shot.png"],
            "reserved": False,
        }
    ]
    assert client.get("/api/agentic-ide/voice-attachments").json()["batches"] == [
        {
            "terminal": name,
            "batch_id": staged["voice_batch_id"],
            "files": ["shot.png"],
            "reserved": False,
        }
    ]

    removed = client.delete(
        f"/api/agentic-ide/terminals/{name}/voice-attachments/"
        f"{staged['voice_batch_id']}"
    )
    assert removed.status_code == 200
    assert client.get(
        f"/api/agentic-ide/terminals/{name}/voice-attachments"
    ).json()["batches"] == []


async def test_voice_staging_refuses_to_also_type_the_loose_path(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"stage_for_voice": "true"},
    )

    assert response.status_code == 422
    assert "deliver=false" in response.json()["detail"]


async def test_a_pane_drop_still_types_and_does_no_analysis(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The cheap path is unchanged — a pane drop must not start paying for a model."""
    name = await _workspace(registry, tmp_path)
    written: list[str] = []
    registry.write = lambda key, text: written.append(text) or True  # type: ignore[assignment]

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach", files=_upload()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is True
    assert body["analysis"] == []
    assert written and "shot.png" in written[0]


async def test_a_document_drop_returns_its_text(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files={"files": ("spec.md", io.BytesIO(b"# Spec\nReturn 202."), "text/markdown")},
        data={"analyze": "true", "deliver": "false"},
    )

    entry = response.json()["analysis"][0]
    assert entry["kind"] == "text"
    assert entry["described_by"] == "extraction"
    assert "Return 202." in entry["detail"]


async def test_without_a_vision_provider_the_drop_still_works_and_says_why(
    client: TestClient, registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install of a downloader whose only key reaches a text-only model."""
    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", lambda: None)
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=_upload(),
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    entry = response.json()["analysis"][0]
    assert entry["detail"] == ""
    assert entry["note"]
    # The file itself still landed and is still referenced.
    assert entry["reference"]
    assert response.json()["copied"] == 1


async def test_two_files_that_sanitize_to_one_name_stay_two(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Each dropped file gets its OWN reference, matched to its own bytes.

    ``safe_name`` maps different names onto the same one ("a b.txt" and
    "a-b.txt" both become "a_b.txt"), and they are stored as two distinct files.
    Pairing them up by name would reference one of them twice and lose the
    other — a file the user watched land, gone.
    """
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=[
            ("files", ("a b.txt", io.BytesIO(b"first file"), "text/plain")),
            ("files", ("a-b.txt", io.BytesIO(b"second file"), "text/plain")),
        ],
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["copied"] == 2
    assert len(body["references"]) == 2
    assert len(set(body["references"])) == 2
    # And each analysis carries the bytes of ITS file, not the other's.
    details = sorted(a["detail"] for a in body["analysis"])
    assert details == ["first file", "second file"]


async def test_an_empty_file_does_not_desynchronise_the_rest(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """An empty entry is skipped on both sides, so nothing shifts by one."""
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        files=[
            ("files", ("empty.txt", io.BytesIO(b""), "text/plain")),
            ("files", ("real.txt", io.BytesIO(b"the real contents"), "text/plain")),
        ],
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["files"] == ["real.txt"]
    assert body["analysis"][0]["detail"] == "the real contents"


async def test_an_empty_drop_is_still_refused(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/attach",
        data={"analyze": "true", "deliver": "false"},
    )

    assert response.status_code == 422


# -------------------------------------------------------------------- prompt
async def test_the_analysis_reaches_the_composed_prompt(
    client: TestClient, registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: what was read out of the file is in what the agent gets."""
    name = await _workspace(registry, tmp_path)
    seen: dict = {}

    async def fake_compose(utterance: str, **kwargs: object):  # noqa: ANN202
        seen["attachments"] = list(kwargs.get("attachments") or [])
        return prompt_composer.ComposedPrompt(
            text=f"## Task\n{utterance}", composed_by="llm"
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={
            "prompt": "fix this",
            "compose": True,
            "dry_run": True,
            "attachments": [
                {
                    "name": "shot.png",
                    "reference": "@.jarvis/drops/shot.png",
                    "kind": "image",
                    "detail": "The submit button overflows its container.",
                    "described_by": "vision",
                    "note": "",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(seen["attachments"]) == 1
    assert isinstance(seen["attachments"][0], DropAnalysis)
    assert "submit button overflows" in seen["attachments"][0].detail


async def test_attachments_without_composition_are_not_silently_dropped(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """A caller that says "here is what the user dropped" is owed delivery of it.

    Without this branch the picture would be described, paid for, shown to the
    user as attached, and then never reach the agent at all.
    """
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={
            "prompt": "fix this",
            "compose": False,
            "dry_run": True,
            "attachments": [
                {
                    "name": "shot.png",
                    "reference": "@shot.png",
                    "kind": "image",
                    "detail": "The submit button overflows its container.",
                    "described_by": "vision",
                    "note": "",
                }
            ],
        },
    )

    body = response.json()
    assert "submit button overflows" in body["composed"]
    assert "fix this" in body["composed"]


async def test_a_plain_prompt_is_untouched_by_the_attachment_path(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    name = await _workspace(registry, tmp_path)

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={"prompt": "run the tests", "compose": False, "dry_run": True},
    )

    body = response.json()
    assert body["composed"] == "run the tests"
    assert body["composed_by"] == "raw"
