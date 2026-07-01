"""Entry point for ``python -m overlay``."""

from __future__ import annotations

import faulthandler
import sys

# Native-crash forensics: Qt aborts (qFatal, STATUS_BREAKPOINT 0x80000003)
# normally print NOTHING to stderr — the process just dies.
# faulthandler.enable() installs a signal handler that writes a Python
# stack trace to stderr on a native crash. Without it, subprocess
# diagnosis is impossible.
faulthandler.enable()

from .main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
