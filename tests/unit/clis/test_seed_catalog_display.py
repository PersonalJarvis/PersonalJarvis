"""Display-quality contract for the curated seed catalog.

The CLI section renders ``display_name`` as the primary card title, so every
entry must carry a name a non-expert can recognize. The Google entries are
pinned explicitly because they are the ones users failed to recognize
("gcloud"? "GAM"?). ASCII umlaut substitutes ("fuer") and German fragments  # i18n-allow: cites the literal forbidden tokens checked below
("uvm.", "Env-Variablen") are rejected because user-facing catalog strings
must be clean English (Output Language Policy).
"""
from __future__ import annotations

import json
import re

from jarvis.clis.catalog import SEED_CATALOG_PATH, load_catalog

# Tokens that only appear in broken ASCII-umlaut German or German fragments.
_FORBIDDEN_TOKENS = re.compile(
    r"\b(fuer|ueber|loeschen|uvm|eintraege|variablen)\b", re.IGNORECASE  # i18n-allow: German fragment tokens matched in logic
)


def test_gcloud_display_name_is_google_cloud_cli() -> None:
    spec = load_catalog()["gcloud"]
    assert spec.display_name == "Google Cloud CLI"


def test_google_workspace_cli_is_discoverable_by_name() -> None:
    spec = load_catalog()["gam"]
    assert spec.display_name == "Google Workspace CLI (GAM)"
    assert spec.category == "workspace"


def test_user_facing_strings_have_no_german_fragments() -> None:
    raw = json.loads(SEED_CATALOG_PATH.read_text(encoding="utf-8"))
    offenders: list[str] = []
    note = raw.get("note", "")
    if _FORBIDDEN_TOKENS.search(note):
        offenders.append(f"note: {note!r}")
    for entry in raw.get("entries", []):
        for field in ("display_name", "description"):
            text = entry.get(field, "")
            if _FORBIDDEN_TOKENS.search(text):
                offenders.append(f"{entry.get('name', '?')}.{field}: {text!r}")
    assert not offenders, "German fragments in user-facing strings:\n" + "\n".join(offenders)
