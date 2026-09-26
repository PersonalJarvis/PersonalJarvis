"""Resolve reported deliverables without trusting model-authored file paths."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit


def reviewable_outputs(values: list[str], workspace: str) -> tuple[list[str], list[str]]:
    """Return display links and independently checked local-file evidence."""
    output: list[str] = []
    evidence: list[str] = []
    root = Path(workspace).resolve() if workspace else None
    for raw in values[:20]:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or any(ord(char) < 32 for char in value):
            continue
        try:
            url = urlsplit(value)
        except ValueError:
            continue
        if url.scheme in {"http", "https"} and url.netloc and not url.username and not url.password:
            output.append(value)
            continue
        if url.scheme and not (len(url.scheme) == 1 and value[1:3] in {":\\", ":/"}):
            continue
        if root is None:
            continue
        try:
            candidate = Path(value)
            candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                path = str(candidate)
                output.append(path)
                evidence.append(path)
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(output)), list(dict.fromkeys(evidence))
