"""Append-only JSON-Lines audit log for self-mod operations.

Plan-§AD-6: no rotation, `threading.Lock` for concurrency safety, dedicated
writer (not stdlib `logging`). Plan-§AP-2 / §AP-5: I/O errors do NOT
propagate — the caller is never crashed; values for secret paths are redacted
before writing so that no plaintext secret is persisted.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, ClassVar

from .schema import AuditEvent

_LOG = logging.getLogger(__name__)

# Path patterns whose `old_value`/`new_value` are redacted in the audit log.
# Substring-based with word boundaries (`.`, `_`, `-`, line boundary) so that
# `tts.provider` is not incorrectly flagged as sensitive.
_SECRET_PATH_RE = re.compile(
    r"(?:^|[._-])(api[_-]?key|password|passwd|token|secret|credential|auth)(?:$|[._-])",
    re.IGNORECASE,
)


def _redact_value(value: Any) -> str | None:
    """Replace a value with '*' characters of the same length.

    The length remains visible for telemetry purposes (e.g. "16-character token")
    while the actual content is hidden.
    """
    if value is None:
        return None
    text = str(value)
    return "*" * len(text) if text else ""


def _is_sensitive_path(path: str | None) -> bool:
    if not path:
        return False
    return bool(_SECRET_PATH_RE.search(path))


class SelfModAudit:
    """Append-only JSON-Lines logger.

    Default path: `data/self_mod.log` relative to the current working
    directory (Plan-§AD-6). Overridable via the `path` constructor argument —
    e.g. in tests via `tmp_path`.
    """

    DEFAULT_PATH: ClassVar[Path] = Path("data") / "self_mod.log"

    def __init__(self, path: Path | str | None = None) -> None:
        self._path: Path = Path(path) if path is not None else self.DEFAULT_PATH
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: AuditEvent) -> None:
        """Write a JSON-Lines entry.

        Never raises — I/O errors are reported via `logging.warning`.
        Sensitive paths are redacted before writing.
        """
        try:
            redacted = self._redact(event)
            line = redacted.to_jsonline()
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8", newline="") as fh:
                    fh.write(line)
                    fh.write("\n")
        except Exception as exc:  # noqa: BLE001 — caller must never crash (Plan-§AP-5)
            _LOG.warning(
                "SelfModAudit.record failed: %s (path=%s)",
                exc,
                self._path,
            )

    @staticmethod
    def _redact(event: AuditEvent) -> AuditEvent:
        """Mask `old_value`/`new_value` for sensitive paths.

        `AuditEvent` is not frozen, but `model_copy(update=...)` is
        semantically clearer here than in-place mutation and preserves
        forward-compatibility fields (`extra="allow"`).
        """
        if not _is_sensitive_path(event.path):
            return event
        return event.model_copy(
            update={
                "old_value": _redact_value(event.old_value),
                "new_value": _redact_value(event.new_value),
            }
        )

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the last `n` audit entries as dicts.

        I/O errors return an empty list instead of crashing. Corrupt
        lines (invalid JSON) are skipped and logged.
        """
        if n <= 0:
            return []
        try:
            if not self._path.exists():
                return []
            with self._lock, self._path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except Exception as exc:  # noqa: BLE001 — reading must never crash
            _LOG.warning("SelfModAudit.tail failed: %s", exc)
            return []

        tail_lines = lines[-n:]
        result: list[dict[str, Any]] = []
        for raw in tail_lines:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                result.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                _LOG.warning("Corrupt audit line skipped: %s", exc)
        return result
