"""Generate ``assets/icons/jarvis.icns`` — the macOS app-bundle icon.

macOS is the only platform that cannot read the ``.ico``/``.png`` pair the rest
of the project ships: ``CFBundleIconFile`` resolves to an ``.icns`` container,
and PyInstaller's ``BUNDLE`` copies exactly that file into
``Contents/Resources``. Without it the Dock, Finder, Spotlight, and the
Cmd-Tab switcher all fall back to the generic executable icon.

Run it from anywhere; it resolves the repository from this file's location:

    python packaging/macos/make_icns.py            # write the icns
    python packaging/macos/make_icns.py --check    # verify only, write nothing

Honest limitation, deliberately not hidden: the largest mascot master in the
repository is 256x256 (``assets/icons/jarvis-gigi-256.png``). Apple's icon set
goes to 1024x1024, so the 512 and 1024 members are upscaled with Lanczos
resampling and are correspondingly soft on a Retina display at large sizes.
Drop a 1024x1024 master next to the 256 one and rerun this script to fix that
for good — :data:`SOURCE_CANDIDATES` prefers the biggest available source.

Pillow's ICNS writer always emits the full ``ic07``/``ic08``/``ic09``/``ic10``/
``ic11``/``ic12``/``ic13``/``ic14`` member set and ignores a ``sizes=``
argument, so the quality knob that actually works is ``append_images``: every
member we hand it is used verbatim instead of Pillow's own default-resampled
resize. That is why this script renders all six distinct sizes itself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ICONS_DIR = REPO_ROOT / "assets" / "icons"
TARGET = ICONS_DIR / "jarvis.icns"

#: Source masters, best first. The first one that exists wins.
SOURCE_CANDIDATES: tuple[str, ...] = (
    "jarvis-1024.png",
    "jarvis-512.png",
    "jarvis-gigi-256.png",
    "jarvis.png",
)

#: The distinct pixel sizes an ``.icns`` written by Pillow contains.
ICNS_SIZES: tuple[int, ...] = (32, 64, 128, 256, 512, 1024)


def _pick_source() -> Path:
    for name in SOURCE_CANDIDATES:
        candidate = ICONS_DIR / name
        if candidate.is_file():
            return candidate
    searched = ", ".join(SOURCE_CANDIDATES)
    raise SystemExit(f"No icon master found in {ICONS_DIR} (looked for: {searched})")


def build(target: Path = TARGET) -> Path:
    """Write ``target`` from the best available master and return its path."""

    try:
        from PIL import Image
    except ModuleNotFoundError as exc:  # pragma: no cover - environment problem
        raise SystemExit(
            "Pillow is required to build the macOS icon: pip install Pillow"
        ) from exc

    source_path = _pick_source()
    with Image.open(source_path) as source:
        master = source.convert("RGBA")
        if master.width != master.height:
            raise SystemExit(
                f"{source_path} is {master.width}x{master.height}; "
                "an app icon master must be square"
            )
        # Rendered here rather than left to Pillow: its writer resizes with the
        # default filter, Lanczos keeps the mascot's thin outline readable at
        # 32 px and its glow clean when upscaled.
        members = [
            master.resize((size, size), Image.Resampling.LANCZOS)
            for size in ICNS_SIZES
        ]
        largest = members[-1]
        target.parent.mkdir(parents=True, exist_ok=True)
        largest.save(target, format="ICNS", append_images=members)
    return target


def verify(target: Path = TARGET) -> list[int]:
    """Read ``target`` back and return the PIXEL sizes it actually carries.

    ``Image.info["sizes"]`` reports ICNS members the way macOS names them —
    ``(logical_width, logical_height, scale)``, so the 1024 px member appears as
    ``(512, 512, 2)``. Comparing the logical number against a pixel number is
    the easy way to "verify" an icon that is in fact fine, so the scale factor
    is multiplied back in here.
    """

    from PIL import Image

    if not target.is_file():
        raise SystemExit(f"{target} does not exist")
    with Image.open(target) as icns:
        if icns.format != "ICNS":
            raise SystemExit(f"{target} is not an ICNS file (read as {icns.format})")
        sizes = sorted({int(w) * int(scale) for w, _h, scale in icns.info["sizes"]})
    missing = [size for size in ICNS_SIZES if size not in sizes]
    if missing:
        raise SystemExit(
            f"{target} is missing icon sizes: {', '.join(str(s) for s in missing)}"
        )
    return sizes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the existing icns instead of rebuilding it.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=TARGET,
        help=f"Where to write the icns (default: {TARGET}).",
    )
    args = parser.parse_args(argv)

    if not args.check:
        source = _pick_source()
        build(args.output)
        print(f"source: {source}")
        print(f"wrote:  {args.output} ({args.output.stat().st_size} bytes)")
    sizes = verify(args.output)
    rendered = ", ".join(f"{size}x{size}" for size in sizes)
    print(f"verified: {args.output} carries {rendered}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
