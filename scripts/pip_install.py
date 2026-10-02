"""Install project requirements with the reviewed native crypto source when needed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402
from scripts.native_crypto_index import pip_options  # noqa: E402

if __name__ == "__main__":
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", *pip_options(), *sys.argv[1:]],
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    raise SystemExit(result.returncode)
