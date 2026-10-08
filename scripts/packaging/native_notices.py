"""Preserve installed distribution notices for the actual frozen module graph.

Distribution metadata cannot describe every library embedded in native code.
The inventory records that boundary; collecting texts is not a compliance verdict.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path, PurePosixPath

_NOTICE_NAME = re.compile(
    r"^(?:(?:third[-_ ]?party[-_ ]?)?(?:licen[cs]es?|notices?)|copying|copyright|authors)"
    r"(?:[._-].*)?$",
    re.I,
)
_NATIVE_NAME = re.compile(r"\.(?:dll|pyd|dylib)$|\.so(?:\..+)?$", re.I)
_CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".dll", ".pyd", ".so", ".dylib", ".exe"}

TocEntry = tuple[str, str, str]


def _relative_path(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ":" in str(path) or ".." in path.parts:
        return None
    return path if path.parts else None


def _component_directory(name: str, version: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9._-]", "-", f"{name}-{version}").strip(".-")
    identity = hashlib.sha256(f"{name}\0{version}".encode()).hexdigest()[:12]
    return f"{label or 'distribution'}-{identity}"


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _is_text_notice(contents: bytes) -> bool:
    encoding = "utf-16" if contents.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = contents.decode(encoding)
    except UnicodeError:
        # Older license files use Latin-1 copyright characters. Bytes still
        # ship unchanged, but binary control characters are never notice text.
        text = contents.decode("latin-1")
    return all(ord(character) >= 32 or character in "\t\n\r\f" for character in text)


def collect_native_notices(
    pure: Iterable[TocEntry],
    binaries: Iterable[TocEntry],
    datas: Iterable[TocEntry],
    output_dir: Path,
    *,
    distributions: Iterable[metadata.Distribution] | None = None,
    module_distributions: Mapping[str, Sequence[str]] | None = None,
) -> list[TocEntry]:
    """Stage exact notice bytes and return additional PyInstaller DATA entries.

    File ownership narrows namespace packages and overlapping distributions.
    A uniquely mapped module is a fallback only when its file list is absent.
    Neither package code nor every package installed in the build environment
    is copied. Output contains package-relative paths, never build-machine paths.
    """
    pure = list(pure)
    graph = [*pure, *binaries, *datas]
    graph_paths = {Path(source).resolve() for _, source, _ in graph if source}
    native_paths: dict[Path, set[str]] = {}
    for destination, source, kind in graph:
        if source and (kind == "BINARY" or _NATIVE_NAME.search(destination)):
            safe_destination = _relative_path(destination)
            if safe_destination is not None:
                native_paths.setdefault(Path(source).resolve(), set()).add(str(safe_destination))

    installed = list(metadata.distributions() if distributions is None else distributions)
    prepared = []
    discovered_modules: dict[str, set[str]] = {}
    for distribution in installed:
        name = distribution.metadata.get("Name", "unknown")
        file_list_problem = None
        try:
            records = list(distribution.files or ())
        except (OSError, ValueError, TypeError, csv.Error):
            # RECORD is not guaranteed to be valid, including in unused tools.
            records = []
            file_list_problem = "distribution_file_list_unreadable"
        if module_distributions is None:
            try:
                top_level = distribution.read_text("top_level.txt") or ""
            except (OSError, UnicodeError):
                top_level = ""
                file_list_problem = file_list_problem or "distribution_module_list_unreadable"
            roots = {value for value in top_level.split() if value.isidentifier()}
            if not roots:
                for record in records:
                    relative = _relative_path(str(record))
                    if relative is None:
                        continue
                    if len(relative.parts) == 1 and relative.suffix not in {".py", ".pyd", ".so"}:
                        continue
                    root = relative.parts[0].partition(".")[0]
                    if root.isidentifier():
                        roots.add(root)
            for root in roots:
                discovered_modules.setdefault(root, set()).add(name)
        prepared.append((name, distribution, records, file_list_problem))
    module_map = discovered_modules if module_distributions is None else module_distributions
    mapped_roots = {
        module.partition(".")[0]: {
            _canonical_name(name)
            for name in module_map.get(module.partition(".")[0], ())
        }
        for module, _, _ in pure
    }
    fallback_names = {next(iter(names)) for names in mapped_roots.values() if len(names) == 1}
    selected = []
    owned_native: set[Path] = set()
    owned_graph_paths: set[Path] = set()
    selected_fallbacks: set[str] = set()
    for name, distribution, records, file_list_problem in prepared:
        owned_paths = {
            Path(distribution.locate_file(record)).resolve()
            for record in records
        } & graph_paths
        if not owned_paths and not (not records and _canonical_name(name) in fallback_names):
            continue
        owned_native.update(owned_paths & native_paths.keys())
        owned_graph_paths.update(owned_paths)
        if not records:
            selected_fallbacks.add(_canonical_name(name))
        selected.append((name, distribution, records, owned_paths, file_list_problem))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    additions: list[TocEntry] = []
    components = []
    for name, distribution, records, owned_paths, file_list_problem in sorted(
        selected, key=lambda item: (_canonical_name(item[0]), item[1].version)
    ):
        version = distribution.version
        directory = _component_directory(name, version)
        declared = [
            str(value).replace("\\", "/")
            for value in distribution.metadata.get_all("License-File", ())
        ]
        distribution_root = Path(distribution.locate_file("")).resolve()
        notices = []
        issues = [{"reason": file_list_problem}] if file_list_problem else []
        matched_declarations: set[str] = set()
        for record in sorted(records, key=str):
            record_name = str(record).replace("\\", "/")
            filename = PurePosixPath(record_name).name
            matches = {
                value for value in declared
                if record_name == value or record_name.endswith("/" + value)
            }
            matched_declarations.update(matches)
            is_declared = bool(matches)
            if not is_declared and not _NOTICE_NAME.fullmatch(filename):
                continue
            relative = _relative_path(record_name)
            if relative is None:
                issues.append({"reason": "unsafe_notice_record"})
                continue
            if any(part.lower() == "__pycache__" for part in relative.parts) or (
                relative.suffix.lower() in _CODE_SUFFIXES
            ):
                issues.append({"file": str(relative), "reason": "executable_or_cache_excluded"})
                continue
            source = Path(distribution.locate_file(record)).resolve()
            if not source.is_relative_to(distribution_root):
                issues.append({"file": str(relative), "reason": "notice_outside_distribution"})
                continue
            try:
                contents = source.read_bytes()
            except OSError:
                # The build inventory exposes the missing text rather than implying success.
                issues.append({"file": str(relative), "reason": "notice_unreadable"})
                continue
            if not _is_text_notice(contents):
                issues.append({"file": str(relative), "reason": "non_text_notice_excluded"})
                continue
            destination = PurePosixPath("licenses", directory, relative)
            staged = output_dir / directory / Path(*relative.parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(contents)
            additions.append((str(destination), str(staged), "DATA"))
            notices.append({
                "file": str(destination),
                "sha256": hashlib.sha256(contents).hexdigest(),
            })
        # importlib.metadata can filter missing RECORD entries. The declared
        # license list must therefore remain visible even when files omits them.
        for value in sorted(set(declared) - matched_declarations):
            relative = _relative_path(value)
            if relative is None:
                issues.append({"reason": "unsafe_notice_record"})
            else:
                issues.append({"file": str(relative), "reason": "declared_notice_missing"})
        expression = distribution.metadata.get("License-Expression")
        label = distribution.metadata.get("License")
        # Some old metadata embeds a complete license here. Ship the actual text
        # files instead of duplicating arbitrary multiline metadata in the index.
        if label and ("\n" in label or len(label) > 256):
            label = None
        components.append({
            "name": name,
            "version": version,
            "declared_license": expression or label,
            "license_metadata_is_authoritative": False,
            "notice_status": "texts_collected" if notices else "no_texts_found",
            "file_list_available": bool(records),
            "license_files": notices,
            "collection_issues": issues,
            "native_files": sorted({
                destination
                for source in owned_paths & native_paths.keys()
                for destination in native_paths[source]
            }),
            "native_transitive_terms": "not_assessed",
            "source_delivery_obligations": "not_assessed",
        })
    inventory = {
        "schema_version": 1,
        "scope": "installed_distribution_notices_for_frozen_graph",
        "compliance_status": "not_assessed",
        "native_transitive_terms": "not_assessed",
        "source_delivery_obligations": "not_assessed",
        "components": components,
        "unmapped_module_roots": sorted({
            module.partition(".")[0]
            for module, source, _ in pure
            if (names := mapped_roots[module.partition(".")[0]])
            and (not source or Path(source).resolve() not in owned_graph_paths)
            and not names & selected_fallbacks
        }),
        "unmapped_native_files": sorted({
            destination
            for source in native_paths.keys() - owned_native
            for destination in native_paths[source]
        }),
    }
    manifest = output_dir / "components.json"
    manifest.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    additions.append(("licenses/components.json", str(manifest), "DATA"))
    return additions
