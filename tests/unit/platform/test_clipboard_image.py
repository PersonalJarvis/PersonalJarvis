"""The picture clipboard: one image, every format the OS knows, on every OS."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from jarvis.platform import clipboard_image


def _png(width: int = 8, height: int = 6, *, alpha: bool = False) -> bytes:
    if alpha:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        image.putpixel((0, 0), (200, 10, 10, 255))
    else:
        image = Image.new("RGB", (width, height), (20, 120, 220))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch):
    def use(platform: str) -> None:
        monkeypatch.setattr(
            clipboard_image, "detect_capabilities", lambda: SimpleNamespace(display_present=True)
        )
        monkeypatch.setattr(clipboard_image, "detect_platform", lambda: platform)

    return use


def test_a_payload_that_is_not_a_png_is_refused() -> None:
    result = clipboard_image.copy_image(b"GIF89a")

    assert result == clipboard_image.CopyResult(False, "not_png")
    assert result.message == "Only PNG pictures can be copied."


def test_a_headless_host_never_touches_the_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clipboard_image, "detect_capabilities", lambda: SimpleNamespace(display_present=False)
    )

    def unexpected(_png: bytes) -> clipboard_image.CopyResult:
        raise AssertionError("no OS integration on a headless host")

    for name in ("_write_windows", "_write_macos", "_write_linux"):
        monkeypatch.setattr(clipboard_image, name, unexpected)

    assert clipboard_image.copy_image(_png()).reason == "no_display"
    assert clipboard_image.write_png(_png()) is False


def test_transparent_pixels_become_white_in_the_bitmap() -> None:
    dib = clipboard_image.png_to_dib(_png(alpha=True))
    # Re-wrap the packed DIB as a BMP file to read it back.
    header = b"BM" + (14 + len(dib)).to_bytes(4, "little") + b"\0\0\0\0"
    header += (14 + int.from_bytes(dib[0:4], "little")).to_bytes(4, "little")
    with Image.open(io.BytesIO(header + dib)) as bitmap:
        rgb = bitmap.convert("RGB")
        assert rgb.getpixel((0, 0)) == (200, 10, 10)
        assert rgb.getpixel((5, 4)) == (255, 255, 255)


def test_the_tiff_rendition_keeps_transparency() -> None:
    tiff = clipboard_image.png_to_tiff(_png(alpha=True))

    with Image.open(io.BytesIO(tiff)) as image:
        assert image.format == "TIFF"
        assert image.mode == "RGBA"
        assert image.getpixel((5, 4))[3] == 0


def test_a_palette_picture_keeps_its_transparency_in_the_tiff() -> None:
    palette = Image.new("RGBA", (4, 4), (0, 0, 0, 0)).convert("P")
    buffer = io.BytesIO()
    palette.save(buffer, "PNG", transparency=0)

    with Image.open(io.BytesIO(clipboard_image.png_to_tiff(buffer.getvalue()))) as image:
        assert image.mode == "RGBA"


def test_the_macos_bridge_is_valid_javascript(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = tmp_path / "bridge.js"
    script.write_text(clipboard_image._MACOS_BRIDGE, encoding="utf-8")  # noqa: SLF001

    assert subprocess.run([node, "--check", str(script)], check=False).returncode == 0


def test_macos_sets_png_and_tiff_on_the_pasteboard(
    monkeypatch: pytest.MonkeyPatch, desktop
) -> None:
    desktop("darwin")
    calls: list[list[str]] = []
    staged: dict[str, bytes] = {}

    def fake_run(command: list[str], data: bytes | None = None) -> tuple[bool, str]:
        calls.append(command)
        pairs = command[5:]
        for kind, path in zip(pairs[0::2], pairs[1::2], strict=True):
            staged[kind] = Path(path).read_bytes()
        return True, "public.png public.tiff"

    monkeypatch.setattr(clipboard_image, "_run", fake_run)
    png = _png()

    result = clipboard_image.copy_image(png)

    assert result.ok and result.formats == ("public.png", "public.tiff")
    assert len(calls) == 1
    assert calls[0][:5] == [
        "/usr/bin/osascript", "-l", "JavaScript", "-e", clipboard_image._MACOS_BRIDGE  # noqa: SLF001
    ]
    assert staged["public.png"] == png
    assert staged["public.tiff"].startswith((b"II*\0", b"MM\0*"))
    # The staged files are gone once the pasteboard holds the data.
    assert not any(Path(path).exists() for path in calls[0][6::2])


def test_macos_falls_back_to_applescript_when_the_bridge_fails(
    monkeypatch: pytest.MonkeyPatch, desktop
) -> None:
    desktop("darwin")
    calls: list[list[str]] = []

    def fake_run(command: list[str], data: bytes | None = None) -> tuple[bool, str]:
        calls.append(command)
        return (False, "") if "JavaScript" in command else (True, "")

    monkeypatch.setattr(clipboard_image, "_run", fake_run)

    result = clipboard_image.copy_image(_png())

    assert result.ok and result.formats == ("public.png",)
    assert len(calls) == 2
    assert "«class PNGf»" in calls[1][2]
    assert calls[1][3].endswith(".png")


def test_linux_without_a_clipboard_tool_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch, desktop
) -> None:
    desktop("linux")
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda _name: None)

    result = clipboard_image.copy_image(_png())

    assert result.reason == "no_tool"
    assert "xclip" in result.message and "wl-clipboard" in result.message


def test_linux_hands_the_png_to_xclip_on_x11(monkeypatch: pytest.MonkeyPatch, desktop) -> None:
    desktop("linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: f"/usr/bin/{name}")
    fed: list[tuple[list[str], bytes | None]] = []
    def fake_run(command: list[str], data: bytes | None = None) -> tuple[bool, str]:
        fed.append((command, data))
        return True, ""

    monkeypatch.setattr(clipboard_image, "_run", fake_run)
    png = _png()

    result = clipboard_image.copy_image(png)

    assert result.ok and result.formats == ("image/png",)
    assert fed == [(["/usr/bin/xclip", "-selection", "clipboard", "-t", "image/png", "-i"], png)]


def test_the_runner_does_not_wait_for_a_forked_clipboard_owner() -> None:
    """xclip and wl-copy leave a child serving the clipboard; it holds stdout."""
    child = "import time; time.sleep(4)"
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "print('served')"
    )
    started = time.monotonic()

    ok, stdout = clipboard_image._run([sys.executable, "-c", parent], b"")  # noqa: SLF001

    assert ok is True
    assert stdout == "served"
    assert time.monotonic() - started < 3.0


def test_a_failing_command_reports_false() -> None:
    ok, _stdout = clipboard_image._run(  # noqa: SLF001
        [sys.executable, "-c", "import sys; sys.stderr.write('no display'); sys.exit(1)"]
    )
    assert ok is False


def test_a_missing_command_reports_false() -> None:
    assert clipboard_image._run(["jarvis-no-such-clipboard-tool"]) == (False, "")  # noqa: SLF001
