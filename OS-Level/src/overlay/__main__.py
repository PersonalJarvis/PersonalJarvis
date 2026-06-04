"""Entry-Point fuer ``python -m overlay``."""

from __future__ import annotations

import faulthandler
import sys

# Native-Crash-Forensik: Qt-Aborts (qFatal, STATUS_BREAKPOINT 0x80000003)
# drucken normalerweise NICHTS auf stderr — der Process stirbt einfach.
# faulthandler.enable() haengt einen Signal-Handler ein, der bei Native-
# Crash einen Python-Stack-Trace nach stderr schreibt. Ohne das ist
# Subprocess-Diagnose unmoeglich.
faulthandler.enable()

from .main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
