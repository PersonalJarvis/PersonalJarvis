"""Catalog package for curated CLI specs.

Provides:

- ``SEED_CATALOG_PATH`` — Path to the shipped ``seed_catalog.json``.
- ``CliCatalog``        — Loader class that merges seed and custom specs.
- ``load_catalog()``    — Convenience function for read-only access.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jarvis.clis.spec import CliSpec, CliSpecModel
from jarvis.core.paths import cli_custom_catalog_path

log = logging.getLogger(__name__)

SEED_CATALOG_PATH: Path = Path(__file__).parent / "seed_catalog.json"


class CliCatalog:
    """Load, cache, and merge seed and custom specs."""

    def __init__(
        self,
        *,
        seed_path: Path | None = None,
        custom_path: Path | None = None,
    ) -> None:
        self._seed_path = seed_path or SEED_CATALOG_PATH
        self._custom_path = custom_path or cli_custom_catalog_path()
        self._cache: dict[str, CliSpec] | None = None

    def all(self) -> dict[str, CliSpec]:
        if self._cache is None:
            self._cache = self._load_all()
        return dict(self._cache)

    def get(self, name: str) -> CliSpec | None:
        return self.all().get(name)

    def refresh(self) -> None:
        self._cache = None

    def register_custom(self, spec: CliSpec) -> None:
        if spec.source != "custom":
            raise ValueError("register_custom() akzeptiert nur source='custom'-Specs")
        customs = self._load_custom_dict()
        customs[spec.name] = _spec_to_json(spec)
        self._write_custom(customs)
        self.refresh()

    def remove_custom(self, name: str) -> bool:
        customs = self._load_custom_dict()
        if name not in customs:
            return False
        del customs[name]
        self._write_custom(customs)
        self.refresh()
        return True

    def seed_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.all().values() if s.source == "seed")

    def custom_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.all().values() if s.source == "custom")

    def _load_all(self) -> dict[str, CliSpec]:
        seed = self._load_seed()
        custom = self._load_custom()
        merged: dict[str, CliSpec] = dict(seed)
        for name, spec in custom.items():
            if name in merged:
                log.info("cli-catalog: custom-Override fuer seed-Eintrag '%s'", name)
            merged[name] = spec
        return merged

    def _load_seed(self) -> dict[str, CliSpec]:
        if not self._seed_path.is_file():
            log.warning("cli-catalog: seed_catalog.json fehlt unter %s", self._seed_path)
            return {}
        try:
            raw = json.loads(self._seed_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.exception("cli-catalog: seed_catalog.json unlesbar: %s", exc)
            return {}
        return self._parse_entries(raw.get("entries", []), source="seed")

    def _load_custom(self) -> dict[str, CliSpec]:
        if not self._custom_path.is_file():
            return {}
        try:
            raw = json.loads(self._custom_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.exception("cli-catalog: custom.json unlesbar: %s", exc)
            return {}
        entries = raw if isinstance(raw, list) else raw.get("entries", [])
        return self._parse_entries(entries, source="custom")

    def _load_custom_dict(self) -> dict[str, dict[str, Any]]:
        if not self._custom_path.is_file():
            return {}
        try:
            raw = json.loads(self._custom_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        entries = raw if isinstance(raw, list) else raw.get("entries", [])
        return {e.get("name", ""): e for e in entries if isinstance(e, dict) and e.get("name")}

    def _parse_entries(self, entries: list[Any], *, source: str) -> dict[str, CliSpec]:
        out: dict[str, CliSpec] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                log.warning("cli-catalog[%s]: entry kein dict: %r", source, entry)
                continue
            entry_with_source = {**entry, "source": source}
            try:
                model = CliSpecModel.model_validate(entry_with_source)
            except ValidationError as exc:
                log.warning(
                    "cli-catalog[%s]: entry '%s' invalid: %s",
                    source, entry.get("name", "?"), exc.errors(include_url=False)[:2],
                )
                continue
            spec = CliSpec.from_model(model)
            out[spec.name] = spec
        return out

    def _write_custom(self, customs: dict[str, dict[str, Any]]) -> None:
        self._custom_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": "1", "entries": list(customs.values())}
        self._custom_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_catalog() -> dict[str, CliSpec]:
    return CliCatalog().all()


def _spec_to_json(spec: CliSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "description": spec.description,
        "homepage": spec.homepage,
        "binary_name": spec.binary_name,
        "check_command": list(spec.check_command),
        "version_parse_regex": spec.version_parse_regex,
        "install": {
            "winget_id": spec.install.winget_id,
            "scoop_package": spec.install.scoop_package,
            "npm_package": spec.install.npm_package,
            "pip_package": spec.install.pip_package,
            "cargo_package": spec.install.cargo_package,
            "script_url": spec.install.script_url,
            "manual_url": spec.install.manual_url,
        },
        "auth": {
            "type": spec.auth.type,
            "login_command": list(spec.auth.login_command) if spec.auth.login_command else None,
            "logout_command": list(spec.auth.logout_command) if spec.auth.logout_command else None,
            "status_command": list(spec.auth.status_command),
            "status_parse": spec.auth.status_parse,
            "secret_keys": list(spec.auth.secret_keys),
            "env_vars": list(spec.auth.env_vars),
        },
        "risk": {
            "default_tier": spec.risk.default_tier,
            "blacklist_patterns": list(spec.risk.blacklist_patterns),
            "whitelist_patterns": list(spec.risk.whitelist_patterns),
        },
        "tool_schema_examples": list(spec.tool_schema_examples),
        "icon": spec.icon,
        "category": spec.category,
        "source": "custom",
    }


__all__ = ["SEED_CATALOG_PATH", "CliCatalog", "load_catalog"]
