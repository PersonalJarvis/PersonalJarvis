"""Machine-readable shape for the preflight commands (``--check``, ``--doctor``).

Installers and setup scripts need to gate on a preflight result. Parsing the
human report is the wrong way to do that: the box-drawing layout is free to
change with every wording fix, and a script that greps it silently starts
lying the moment it does.

This module is the single home for the machine contract those scripts bind to.
Both preflight commands emit **JSON Lines** — one self-contained JSON object
per line, no enclosing array — so a caller can stream them, and a truncated
run still yields valid records for everything that already ran.

Each record is::

    {"component": "ram", "status": "ok", "message": "31.9 GB total", "hint": null}

``status`` is a CLOSED set, which is the whole point: a script branches on it.
The four values mirror :class:`jarvis.diagnostics.doctor.DoctorFinding` so the
two commands never drift into two vocabularies:

  ``ok``    — present and usable.
  ``warn``  — usable but degraded, or configured-but-inert. Not fatal.
  ``fail``  — something advertised cannot work. This is what an installer
              should stop on.
  ``info``  — a neutral fact (detected CPU, chosen model). Never a verdict, and
              never a reason to fail a script.

Exit codes are deliberately NOT changed by asking for JSON: ``--check`` keeps
returning 0 and ``--doctor`` keeps returning 1 on any ``fail``, so callers that
already gate on the exit status keep working unmodified.
"""
from __future__ import annotations

import json
from typing import Final, Literal

Status = Literal["ok", "warn", "fail", "info"]

#: The closed status vocabulary, exported so tests and callers can assert
#: against one list instead of restating it.
STATUSES: Final[tuple[str, ...]] = ("ok", "warn", "fail", "info")

#: Field order in every emitted record. Fixed so a diff of two runs is
#: readable and a golden-file test is stable.
FIELDS: Final[tuple[str, ...]] = ("component", "status", "message", "hint")


def record(
    component: str,
    status: Status,
    message: str,
    hint: str | None = None,
) -> dict[str, object]:
    """Build one preflight record, rejecting an out-of-vocabulary status.

    The guard is not defensive noise: an unknown status would reach a caller's
    ``if status == ...`` branch and be silently treated as "not a failure",
    which is exactly the class of bug the closed set exists to prevent.
    """
    if status not in STATUSES:
        raise ValueError(
            f"status {status!r} is not one of {STATUSES} — the preflight "
            f"vocabulary is closed so scripts can branch on it"
        )
    return {
        "component": component,
        "status": status,
        "message": message,
        "hint": hint,
    }


def dumps(records: list[dict[str, object]]) -> str:
    """Render records as JSON Lines.

    ``ensure_ascii=False`` keeps a detected device name readable rather than
    escaping it; the stream is UTF-8 like every other Jarvis stdout.
    """
    return "\n".join(
        json.dumps({field: rec.get(field) for field in FIELDS}, ensure_ascii=False)
        for rec in records
    )
