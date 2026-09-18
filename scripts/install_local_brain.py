"""Install the keyless local brain: llama.cpp ``llama-server`` + one GGUF model.

Usage::

    python scripts/install_local_brain.py            # binary + default model
    python scripts/install_local_brain.py --model-url https://.../x.gguf
    python scripts/install_local_brain.py --skip-model

Everything lands in ``<user_data_dir>/llama`` (``bin/`` and ``models/``), which
is where :mod:`jarvis.local_models.llama_server` looks. Sources are the
official ggml-org/llama.cpp GitHub releases and a Hugging Face model repo; no
account and no key are involved. The binary flavour follows the machine: CUDA
on Windows with an NVIDIA driver, CPU on Windows otherwise, the Ubuntu build
on Linux, the arm64/x64 build on macOS.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.local_models.llama_server import llama_home  # noqa: E402

RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"

#: Qwen3.5-4B (2026-02, Apache-2.0) at Q4_K_M: ~2.6 GB, fully GPU-resident on a
#: 4 GB card at 8K context, and a reasonable CPU model where there is no GPU.
DEFAULT_MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"
)

#: Its vision projector (F16, ~670 MB). Installed next to the model as
#: ``mmproj-<model>.gguf``, it lets the local brain read screenshots; the
#: managed server keeps it on the CPU so VRAM planning is unchanged.
DEFAULT_MMPROJ_URL = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/mmproj-F16.gguf"


def _asset_patterns() -> list[str]:
    system = sys.platform
    machine = platform.machine().lower()
    if system == "win32":
        arch = "arm64" if "arm" in machine else "x64"
        pats = [f"bin-win-cpu-{arch}.zip"]
        if shutil.which("nvidia-smi") and arch == "x64":
            pats.insert(0, "bin-win-cuda-12.4-x64.zip")
        return pats
    if system == "darwin":
        return ["bin-macos-arm64.zip" if "arm" in machine else "bin-macos-x64.zip"]
    return ["bin-ubuntu-x64.zip"]


def _fetch(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out, length=1 << 20)
    tmp.replace(dest)


def install_binary(home: Path) -> str:
    with urllib.request.urlopen(RELEASES_API, timeout=30) as resp:  # noqa: S310
        releases = json.load(resp)
    patterns = _asset_patterns()
    for rel in releases:
        names = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        for pat in patterns:
            main = next((n for n in names if n.startswith("llama-") and n.endswith(pat)), None)
            if not main:
                continue
            bin_dir = home / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            wanted = [main]
            if "cuda" in pat:
                cudart = next(
                    (n for n in names if n.startswith("cudart-") and "12.4-x64" in n), None
                )
                if not cudart:
                    continue
                wanted.append(cudart)
            with tempfile.TemporaryDirectory() as tmpdir:
                for name in wanted:
                    zpath = Path(tmpdir) / name
                    _fetch(names[name], zpath)
                    with zipfile.ZipFile(zpath) as zf:
                        zf.extractall(bin_dir)
            (bin_dir / "VERSION").write_text(rel["tag_name"] + "\n", encoding="utf-8")
            return f"{rel['tag_name']} ({pat})"
    raise SystemExit(f"No llama.cpp release asset matches {patterns}")


def install_model(home: Path, url: str) -> Path:
    models = home / "models"
    models.mkdir(parents=True, exist_ok=True)
    dest = models / url.rsplit("/", 1)[-1].split("?", 1)[0]
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  model already present: {dest.name}")
        return dest
    _fetch(url, dest)
    return dest


def install_mmproj(model: Path, url: str) -> Path:
    dest = model.parent / f"mmproj-{model.stem}.gguf"
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  vision projector already present: {dest.name}")
        return dest
    _fetch(url, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-url", default=DEFAULT_MODEL_URL)
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--skip-binary", action="store_true")
    ap.add_argument("--mmproj-url", default=DEFAULT_MMPROJ_URL)
    ap.add_argument("--skip-vision", action="store_true", help="text only, no screen reading")
    args = ap.parse_args()
    home = llama_home()
    print(f"Local brain home: {home}")
    if not args.skip_binary:
        print(f"llama.cpp: {install_binary(home)}")
    if not args.skip_model:
        model = install_model(home, args.model_url)
        print(f"model: {model.name}")
        # The projector belongs to the default model; a custom model URL needs
        # its own matching projector, so it is only fetched for the default.
        if not args.skip_vision and args.model_url == DEFAULT_MODEL_URL:
            print(f"vision: {install_mmproj(model, args.mmproj_url).name}")
    print("Done. Restart Jarvis (or wait for the next boot) to start the local brain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
