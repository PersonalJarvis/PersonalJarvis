"""Chat links to a file or folder must resolve to that path, and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.platform.open_path import ChatOpenRejected, chat_link_path, prepare_chat_open


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"C:\Users\me\Clip.mp4", r"C:\Users\me\Clip.mp4"),
        ("C:/Users/me/Clip.mp4", "C:/Users/me/Clip.mp4"),
        ("file:///C:/Users/me/My%20Clip.mp4", "C:/Users/me/My Clip.mp4"),
        ("file://C:/Users/me/Clip.mp4", "C:/Users/me/Clip.mp4"),
        ("file://localhost/C:/Users/me/Clip.mp4", "C:/Users/me/Clip.mp4"),
        ("file:///home/me/clip.mp4", "/home/me/clip.mp4"),
        ("<C:/Users/me/Clip.mp4>", "C:/Users/me/Clip.mp4"),
        ("~/Downloads/clip.mp4", "~/Downloads/clip.mp4"),
        ("/home/me/Videos/clip.mp4", "/home/me/Videos/clip.mp4"),
    ],
)
def test_local_paths_are_recognized(raw: str, expected: str) -> None:
    assert chat_link_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "clip.mp4",
        "./clip.mp4",
        r"\\server\share\clip.mp4",
        "file://server/share/clip.mp4",
        "https://example.test/clip.mp4",
        "javascript:alert(1)",
        "/api/outputs/run/files/clip.mp4/download",
        "",
    ],
)
def test_web_relative_and_network_paths_are_not_local_opens(raw: str) -> None:
    assert chat_link_path(raw) is None


def test_prepare_opens_an_existing_file_or_folder(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    folder = tmp_path / "exports"
    folder.mkdir()
    assert prepare_chat_open(str(clip)) == clip.resolve()
    assert prepare_chat_open(clip.as_uri()) == clip.resolve()
    assert prepare_chat_open(str(folder)) == folder.resolve()


def test_prepare_refuses_a_program(tmp_path: Path) -> None:
    program = tmp_path / "run.exe"
    program.write_bytes(b"nope")
    with pytest.raises(ChatOpenRejected) as caught:
        prepare_chat_open(str(program))
    assert caught.value.reason == "refused-program"


def test_prepare_misses_a_path_that_is_not_there(tmp_path: Path) -> None:
    missing = tmp_path / "gone.mp4"
    with pytest.raises(ChatOpenRejected) as caught:
        prepare_chat_open(str(missing))
    assert caught.value.reason == "not-found"


def test_prepare_rejects_a_relative_name(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(ChatOpenRejected) as caught:
        prepare_chat_open("clip.mp4")
    assert caught.value.reason == "not-a-local-path"
