"""``drop_analysis`` — reading what a dropped file actually contains.

The point of this module is that a coding agent gets the CONTENT of a dropped
screenshot, not just a path to it. So the tests care about two things above all:
that the content really travels, and that when it cannot (no vision-capable
provider, a failing one, a scanned PDF) the result says so out loud instead of
returning something that reads like a description.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import drop_analysis
from jarvis.agentic_ide.drop_analysis import DropAnalysis, analyze
from jarvis.brain.drop_context import DroppedItem


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content


class _SeeingBrain:
    """A vision-capable brain that reports what it was handed."""

    supports_vision = True

    def __init__(self, answer: str = "A login form with the submit button cut off.") -> None:
        self.answer = answer
        self.calls: list[object] = []

    async def complete(self, request):  # noqa: ANN001, ANN201
        self.calls.append(request)
        yield _Delta(self.answer)


class _BrokenBrain:
    supports_vision = True

    async def complete(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("provider is down")
        yield  # pragma: no cover - makes this an async generator


def _image(name: str = "shot.png") -> DroppedItem:
    # A one-pixel PNG: real bytes, so nothing has to special-case a fake.
    data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return DroppedItem(name=name, mime="image/png", data=data)


async def test_image_is_described_and_the_description_travels() -> None:
    brain = _SeeingBrain()

    result = await analyze([(_image(), "@.jarvis/drops/shot.png")], brain=brain)

    assert len(result) == 1
    assert result[0].kind == "image"
    assert result[0].described_by == "vision"
    assert "submit button cut off" in result[0].detail
    # The reference has to survive alongside the description: an agent that CAN
    # open the picture should still be pointed at it.
    assert result[0].reference == "@.jarvis/drops/shot.png"
    assert len(brain.calls) == 1
    # The picture must actually reach the model — a text-only call would produce
    # a confident description of nothing.
    assert brain.calls[0].messages[0].images


async def test_no_vision_provider_says_so_rather_than_inventing(monkeypatch) -> None:
    # The install every open-source downloader might have: keys that reach a
    # text-only provider and nothing that can see. ``brain=None`` means "resolve
    # one", so the absence has to be staged at the resolver.
    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", lambda: None)

    result = await analyze([(_image(), "@shot.png")], brain=None)

    assert len(result) == 1
    assert result[0].detail == ""
    assert result[0].described_by == "none"
    assert "not described" in result[0].note.lower()


async def test_a_document_only_drop_never_resolves_a_vision_brain(monkeypatch) -> None:
    # Resolving one loads config and walks the provider registry; a drop with no
    # picture in it has no reason to pay for that.
    def _boom() -> None:
        raise AssertionError("a document-only drop must not resolve a vision brain")

    monkeypatch.setattr(drop_analysis, "_resolve_vision_brain", _boom)
    item = DroppedItem(name="notes.txt", mime="text/plain", data=b"hello")

    result = await analyze([(item, '"notes.txt"')], brain=None)

    assert result[0].described_by == "extraction"


async def test_a_failing_provider_costs_the_description_and_nothing_else() -> None:
    result = await analyze([(_image(), "@shot.png")], brain=_BrokenBrain())

    assert len(result) == 1
    assert result[0].described_by == "none"
    assert result[0].note  # honest, and names nothing it did not do
    assert "cut off" not in result[0].detail


async def test_text_documents_are_extracted_without_a_model() -> None:
    item = DroppedItem(name="spec.md", mime="text/markdown", data=b"# Spec\nDo the thing.")

    result = await analyze([(item, '"spec.md"')], brain=None)

    assert result[0].kind == "text"
    assert result[0].described_by == "extraction"
    assert "Do the thing." in result[0].detail


async def test_a_binary_file_is_reported_as_attached_not_as_read() -> None:
    item = DroppedItem(name="archive.zip", mime="application/zip", data=b"PK\x03\x04rest")

    result = await analyze([(item, '"archive.zip"')], brain=None)

    assert result[0].kind == "other"
    assert result[0].detail == ""
    assert "attached" in result[0].note.lower()


async def test_images_beyond_the_cap_are_named_not_silently_dropped() -> None:
    brain = _SeeingBrain()
    pairs = [
        (_image(f"shot{i}.png"), f"@shot{i}.png")
        for i in range(drop_analysis.MAX_IMAGES_ANALYZED + 2)
    ]

    result = await analyze(pairs, brain=brain)

    # Every dropped file still comes back — the user saw a chip for each one.
    assert len(result) == len(pairs)
    described = [r for r in result if r.described_by == "vision"]
    assert len(described) == drop_analysis.MAX_IMAGES_ANALYZED
    skipped = [r for r in result if r.described_by == "none"]
    assert all(str(drop_analysis.MAX_IMAGES_ANALYZED) in r.note for r in skipped)


async def test_a_mixed_drop_describes_the_image_and_extracts_the_document() -> None:
    brain = _SeeingBrain()
    pairs = [
        (_image(), "@shot.png"),
        (DroppedItem(name="notes.txt", mime="text/plain", data=b"line one"), '"notes.txt"'),
    ]

    result = await analyze(pairs, brain=brain)

    assert [r.kind for r in result] == ["image", "text"]
    assert result[0].described_by == "vision"
    assert result[1].described_by == "extraction"


async def test_an_empty_drop_is_not_an_error() -> None:
    assert await analyze([]) == []


async def test_a_long_extraction_is_shortened_and_says_that_it_was() -> None:
    body = b"x" * (drop_analysis.MAX_TOTAL_CHARS + 5_000)
    pairs = [
        (DroppedItem(name=f"big{i}.txt", mime="text/plain", data=body), f'"big{i}.txt"')
        for i in range(6)
    ]

    result = await analyze(pairs, brain=None)

    assert len(result) == len(pairs)  # nothing falls off the end
    assert sum(len(r.detail) for r in result) <= drop_analysis.MAX_TOTAL_CHARS
    # Whatever the TOTAL budget cut has to admit it — a shortened file that
    # reads complete is exactly the quiet loss this bound must not cause. (A
    # file at the per-file cap is not "cut" in that sense; it is the normal
    # bounded extraction.)
    trimmed = [r for r in result if len(r.detail) < drop_analysis.MAX_TEXT_CHARS]
    assert trimmed
    assert all(r.note for r in trimmed)


def test_from_dict_survives_a_malformed_payload() -> None:
    # The wire is a JSON body; a bad one must not 500 a prompt the user is
    # waiting on.
    item = DropAnalysis.from_dict({"name": "a.png", "kind": "nonsense", "detail": 5})

    assert item.name == "a.png"
    assert item.kind == "other"
    assert item.detail == ""


def test_to_dict_and_back_is_lossless() -> None:
    original = DropAnalysis(
        name="shot.png",
        reference="@shot.png",
        kind="image",
        detail="what it shows",
        described_by="vision",
    )

    assert DropAnalysis.from_dict(original.to_dict()) == original


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("a.png", "image/png", "image"),
        # Drag-drop MIME is unreliable — a .py handed over as octet-stream is
        # still text, and the extension is what says so.
        ("a.py", "application/octet-stream", "text"),
        ("a.pdf", "application/octet-stream", "pdf"),
        ("a.bin", "application/octet-stream", "other"),
    ],
)
def test_kind_matches_the_shared_register(name: str, mime: str, expected: str) -> None:
    assert drop_analysis._kind_of(DroppedItem(name=name, mime=mime, data=b"x")) == expected
