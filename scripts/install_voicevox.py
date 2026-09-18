"""Install VOICEVOX Engine (keyless Japanese neural TTS) for Jarvis.

Usage::

    python scripts/install_voicevox.py              # latest stable CPU build
    python scripts/install_voicevox.py --tag 0.25.2

Downloads the official release from github.com/VOICEVOX/voicevox_engine into
``<user_data_dir>/voicevox`` and unpacks it; :mod:`jarvis.plugins.tts.voicevox_engine`
finds and starts it from there. Windows needs 7-Zip on PATH or in its default
folder; Linux/macOS builds are packaged the same way. Roughly 1.8 GB.

The engine's characters are free to use with a credit line such as
"VOICEVOX:<character>" where the audio is published; see each character's
terms on voicevox.hiroshiba.jp.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.plugins.tts.voicevox_engine import engine_home  # noqa: E402

RELEASES = "https://api.github.com/repos/VOICEVOX/voicevox_engine/releases?per_page=20"


def _flavour() -> str:
    if sys.platform == "win32":
        return "windows-cpu"
    if sys.platform == "darwin":
        return "macos-arm64" if "arm" in platform.machine().lower() else "macos-x64"
    return "linux-cpu-x64"


def _seven_zip() -> str:
    for candidate in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe"):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("7-Zip is required to unpack the engine (https://www.7-zip.org/).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default="", help="release tag; default: latest stable")
    args = ap.parse_args()
    with urllib.request.urlopen(RELEASES, timeout=30) as resp:  # noqa: S310
        releases = json.load(resp)
    flavour = _flavour()
    for rel in releases:
        if args.tag and rel["tag_name"] != args.tag:
            continue
        if not args.tag and rel.get("prerelease"):
            continue
        parts = sorted(
            a for a in rel["assets"]
            if f"-{flavour}-" in a["name"] and ".7z." in a["name"] and not a["name"].endswith(".txt")
        , key=lambda a: a["name"])
        if not parts:
            continue
        home = engine_home()
        home.mkdir(parents=True, exist_ok=True)
        for asset in parts:
            dest = home / asset["name"]
            print(f"downloading {asset['name']} ({asset['size'] // 1_000_000} MB)")
            with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as src:  # noqa: S310
                with open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)
        first = home / parts[0]["name"]
        subprocess.run([_seven_zip(), "x", "-y", str(first)], cwd=home, check=True)  # noqa: S603
        for asset in parts:
            (home / asset["name"]).unlink(missing_ok=True)
        (home / "VERSION").write_text(rel["tag_name"] + "\n", encoding="utf-8")
        print(f"VOICEVOX {rel['tag_name']} installed in {home}")
        return 0
    raise SystemExit(f"No VOICEVOX release asset found for {flavour}.")


if __name__ == "__main__":
    raise SystemExit(main())
