"""Read-only, agent-scoped inventory of current memory and learned skill files."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any


def _roots(runtime: Any, agent: Any) -> dict[str, Path]:
    skills = runtime.skills_for(agent.agent_id).root
    # Resolve the configured data root, but never a link substituting the
    # agent's namespace (e.g. macOS /var -> /private/var is a valid data root).
    data_root = skills.parents[2].resolve()
    return {
        "memory": runtime.memory.namespace(runtime.memory.root().resolve(), agent.agent_id),
        "skills": data_root / "society" / agent.agent_id / "skills",
    }


def _contained(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or any(p in {".", ".."} or p.startswith(".") for p in parts):
        raise ValueError("Invalid knowledge path")
    if "\\" in relative or ":" in relative or relative.startswith("/"):
        raise ValueError("Invalid knowledge path")
    # A link at the namespace root must not substitute another agent's folder.
    if root.resolve() != root.absolute():
        raise ValueError("Linked knowledge namespace")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != ".md":
        raise ValueError("Knowledge path outside this agent")
    return path


def list_files(runtime: Any, agent: Any) -> list[dict[str, Any]]:
    files = []
    for kind, root in _roots(runtime, agent).items():
        if not root.exists() or root.resolve() != root.absolute():
            continue
        for candidate in root.rglob("*.md"):
            relative = candidate.relative_to(root).as_posix()
            try:
                path = _contained(root, relative)
            except ValueError:
                continue  # Hidden history and escaped links are not current knowledge.
            stat = path.stat()
            if not path.is_file():
                continue
            files.append(
                {
                    "path": f"{kind}/{relative}",
                    "name": relative,
                    "kind": kind,
                    "updated_ms": int(stat.st_mtime * 1000),
                    "size": stat.st_size,
                }
            )
    return sorted(files, key=lambda f: (f["path"] != "memory/memory.md", f["kind"], f["name"]))


def read_file(runtime: Any, agent: Any, requested: str) -> dict[str, Any]:
    kind, separator, relative = requested.partition("/")
    roots = _roots(runtime, agent)
    if not separator or kind not in roots:
        raise ValueError("Unknown knowledge collection")
    path = _contained(roots[kind], relative)
    content = path.read_text(encoding="utf-8")
    return {"path": requested, "content": content, "updated_ms": int(path.stat().st_mtime * 1000)}
