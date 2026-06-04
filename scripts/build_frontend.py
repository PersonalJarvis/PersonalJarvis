"""Baut React-Frontend. Wird vor PyInstaller und optional in CI aufgerufen."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "jarvis" / "ui" / "web" / "frontend"


def main() -> int:
    if not (FE / "node_modules").exists():
        subprocess.check_call(["npm", "ci"], cwd=FE)
    subprocess.check_call(["npm", "run", "build"], cwd=FE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
