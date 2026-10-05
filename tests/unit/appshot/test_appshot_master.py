"""The user's lossless copy of an appshot: encoding, redaction, markings, saving."""

from __future__ import annotations

import io
import time

import numpy as np
from PIL import Image

from jarvis.appshot import card_actions, library
from jarvis.appshot.markup import Frame, Hide, Markup, apply_to_scrgb
from jarvis.appshot.master import encode_master
from jarvis.appshot.store import Appshot, AppshotStore
from jarvis.platform.hdr_image import read_png_cicp
from jarvis.screen_context.models import MasterImage, RedactionHit, RedactionRule
from jarvis.screen_context.service import _redacted_master

WHITE = 240.0
SDR_WHITE = WHITE / 80.0


def _shot(**fields) -> Appshot:
    base = dict(
        id="a" * 32, image=b"jpeg", mime="image/jpeg", width=4, height=2, label="selected area",
        app_name="", note="", ui_text="", trigger="hotkey", taken_at=time.time(),
    )
    return Appshot(**{**base, **fields})


def _overlay(size: tuple[int, int], box: tuple[int, int, int, int]) -> bytes:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.paste((255, 0, 0, 255), box)
    buffer = io.BytesIO()
    layer.save(buffer, format="PNG")
    return buffer.getvalue()


def test_sdr_master_is_full_resolution_png_with_the_monitor_profile() -> None:
    profile = b"\0" * 36 + b"acsp" + b"\0" * 100
    pixels = np.full((6, 8, 3), 77, np.uint8)
    files = encode_master(MasterImage(pixels=pixels, icc_profile=profile))
    picture = Image.open(io.BytesIO(files.png))
    assert (files.width, files.height, picture.size) == (8, 6, (8, 6))
    assert picture.info.get("icc_profile") == profile
    assert np.asarray(picture.convert("RGB"))[0, 0].tolist() == [77, 77, 77]
    assert files.hdr_png is None


def test_hdr_master_gives_an_sdr_png_and_a_tagged_hdr_png() -> None:
    scrgb = np.zeros((4, 6, 4), np.float16)
    scrgb[..., :3] = SDR_WHITE
    scrgb[0, 0, :3] = 2 * SDR_WHITE  # a highlight above SDR white
    files = encode_master(MasterImage(pixels=scrgb, hdr=True, sdr_white_nits=WHITE))
    sdr = np.asarray(Image.open(io.BytesIO(files.png)).convert("RGB"))
    assert sdr[1, 1].tolist() == [255, 255, 255]
    assert read_png_cicp(files.hdr_png) == (9, 16, 0, 1)


def test_markings_on_hdr_keep_untouched_pixels_at_full_depth() -> None:
    scrgb = np.full((10, 10, 4), 2 * SDR_WHITE, np.float32)  # all highlight
    markup = Markup(overlay_png=_overlay((10, 10), (0, 0, 3, 3)))
    out = apply_to_scrgb(scrgb, markup, WHITE)
    assert out[5, 5].tolist() == [2 * SDR_WHITE] * 3  # untouched: HDR value kept
    assert out[1, 1, 0] == np.float32(SDR_WHITE)  # red mark at SDR white
    assert out[1, 1, 1] == 0.0


def test_hidden_patches_on_hdr_are_replaced_whole() -> None:
    scrgb = np.zeros((20, 20, 4), np.float32)
    scrgb[5:15, 5:15, :3] = 3 * SDR_WHITE  # bright secret
    markup = Markup(hides=(Hide("pixelate", (0.0, 0.0, 1.0, 1.0)),))
    out = apply_to_scrgb(scrgb, markup, WHITE)
    assert float(out.max()) <= SDR_WHITE + 1e-3  # no highlight survives the patch


def test_a_background_frame_surrounds_the_hdr_picture() -> None:
    scrgb = np.full((40, 60, 4), 2 * SDR_WHITE, np.float32)
    framed = apply_to_scrgb(scrgb, Markup(frame=Frame("dawn", padding=0.1, radius=0)), WHITE)
    assert framed.shape[:2] == (52, 72)
    assert framed[26, 36].tolist() == [2 * SDR_WHITE] * 3
    assert float(framed[0, 0].max()) <= SDR_WHITE + 1e-3


def test_hdr_master_gets_the_same_black_boxes_as_the_model_picture() -> None:
    master = MasterImage(pixels=np.full((10, 10, 4), 1.0, np.float16), hdr=True)
    hit = RedactionHit(rule=RedactionRule.SENSITIVE_PATTERN, label="card", region=(2, 3, 4, 2))
    redacted = _redacted_master(master, None, (hit,))
    pixels = redacted.pixels.astype(np.float32)
    assert pixels[3:5, 2:6, :3].max() == 0.0
    assert pixels[0, 0, 0] == 1.0
    assert np.asarray(master.pixels)[3, 2, 0] == 1.0  # the input is not changed


def test_sdr_master_is_the_redacted_frame_itself() -> None:
    redacted = Image.new("RGB", (3, 2), (0, 0, 0))
    master = _redacted_master(MasterImage(pixels=None), redacted, ())
    assert master.pixels.shape == (2, 3, 3)


def test_an_edit_drops_the_lossless_copies_of_the_unedited_picture() -> None:
    store = AppshotStore()
    store.remember(_shot(original_png=b"orig", hdr_png=b"hdr"), keep_s=60)
    edited = store.replace_image("a" * 32, b"png", "image/png", 4, 2)
    assert edited.original_png == b"" and edited.hdr_png == b""


def test_update_swaps_in_the_finished_copy_without_moving_it() -> None:
    store = AppshotStore()
    store.remember(_shot(), keep_s=60)
    store.remember(_shot(id="b" * 32), keep_s=60)
    store.update(_shot(original_png=b"orig"))
    assert store.get("a" * 32).original_png == b"orig"
    assert store.latest().id == "b" * 32


def test_save_writes_the_lossless_png_and_the_hdr_one_beside_it(tmp_path) -> None:
    png = encode_master(MasterImage(pixels=np.zeros((2, 4, 3), np.uint8))).png
    path = card_actions.save_shot_to_downloads(
        _shot(original_png=png, hdr_png=b"\x89PNGhdr"), folder=tmp_path, now=0
    )
    assert path.read_bytes() == png
    assert path.with_name(f"{path.stem}-hdr.png").read_bytes() == b"\x89PNGhdr"


def test_library_keeps_the_lossless_copy_and_the_hdr_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(library, "_root_override", tmp_path)
    png = encode_master(MasterImage(pixels=np.zeros((5, 7, 3), np.uint8))).png
    shot = _shot(original_png=png, hdr_png=b"\x89PNGhdr")
    assert library.save(shot)
    folder = next(p for p in tmp_path.iterdir() if p.is_dir())
    names = sorted(p.name for p in folder.iterdir() if p.suffix == ".png")
    assert len(names) == 2 and any(n.endswith("-hdr.png") for n in names)
    main = next(folder / n for n in names if not n.endswith("-hdr.png"))
    assert main.read_bytes() == png
