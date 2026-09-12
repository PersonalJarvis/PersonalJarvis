#!/usr/bin/env python3
"""Blocking gate: plugin browser-auth contract (see docs/marketplace/browser-auth-standard.md).

A new auth-bearing plugin is NOT standard-ready / releasable when its normal
user path needs manual tokens, client IDs, secrets, or developer
registrations. This gate inspects the ACTUAL catalog configuration — not a
metadata flag — and fails the build when:

  1. A tracked catalog entry or agent-plugin mirror carries a confidential
     OAuth client SECRET value (names are fine; values never ship).
  2. A DCR discovery_url has no host (the Apollo/Granola outage class).
  3. A PKCE plugin with a placeholder client_id declares no
     oauth_client_family (the expert override would have nowhere to go).
  4. A declared oauth_client_family has no writable BYO secret slots AND no
     publisher shared-client slots in jarvis/setup/wizard.py.
  5. A seed plugin id is missing from docs/marketplace/plugin-auth-audit.md
     (every auth-bearing plugin needs an audited row: ready / fallback /
     blocked + owner of the remaining step).
  6. A PAT entry uses a non-https validation endpoint or documents no
     accepted token prefix.

Stdlib only, cross-platform (pathlib, UTF-8). Exit 0 = clean, 1 = findings.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
SEED = ROOT / "jarvis" / "marketplace" / "seed_catalog.json"
MIRRORS = ROOT / "jarvis" / "marketplace" / "plugins"
WIZARD = ROOT / "jarvis" / "setup" / "wizard.py"
AUDIT = ROOT / "docs" / "marketplace" / "plugin-auth-audit.md"
E2E_AUDIT = ROOT / "docs" / "marketplace" / "plugin-e2e-audit.json"

# This historical inventory is intentionally fixed: adding a BLOCKED row must
# never grant a new built-in plugin permission to ship without a real journey.
LEGACY_PLUGIN_IDS = frozenset(
    """
github vercel supabase notion slack linear stripe cloudflare discord telegram
asana google_drive gmail google_calendar todoist clickup dropbox canva airtable
cal_com home_assistant spotify youtube_music higgsfield outlook onedrive teams
sharepoint onenote microsoft_todo aws azure google_cloud gitlab agentmail x
linkedin meta youtube_studio hubspot apollo salesforce granola zoom figma amd_gpu
""".split()
)
E2E_STAGES = ("ui", "browser", "callback", "connected", "smoke", "reconnect", "restart")
FINGERPRINT_FIELDS = (
    "auth",
    "fallback_auth",
    "mcp_server",
    "native_tool",
    "oauth_client_family",
    "browser_flow",
)
BROWSER_AUTH_MODES = frozenset(
    ("hosted_mcp_oauth_dcr", "oauth_device_flow", "oauth_pkce_loopback", "instance_browser")
)


def config_fingerprint(plugin: dict) -> str:
    """Bind observed evidence to the exact auth and execution configuration."""
    config = {field: plugin.get(field) for field in FINGERPRINT_FIELDS}
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_e2e_audit(plugins: list[dict], audit: object, *, strict: bool = False) -> list[str]:
    """Validate attestations; evidence is manually observed, never inferred here.

    CI tolerates explicit historical external blockers. Release qualification
    uses --require-e2e-pass and accepts only completed journeys.
    """
    if not isinstance(audit, dict) or audit.get("schema_version") != 1:
        return ["e2e: expected audit schema_version 1"]
    rows = audit.get("plugins")
    if not isinstance(rows, list):
        return ["e2e: plugins must be an array"]
    findings: list[str] = []
    by_id: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("plugin_id"), str):
            findings.append("e2e: malformed plugin audit row")
            continue
        pid = row["plugin_id"]
        if pid in by_id:
            findings.append(f"{pid}: duplicate e2e audit row")
        by_id[pid] = row
    catalog_ids = {plugin["id"] for plugin in plugins}
    for pid in by_id.keys() - catalog_ids:
        findings.append(f"{pid}: e2e row has no catalog entry")
    for plugin in plugins:
        pid = plugin["id"]
        row = by_id.get(pid)
        if row is None:
            findings.append(f"{pid}: missing e2e audit row")
            continue
        result = row.get("result")
        if result not in ("PASS", "BLOCKED"):
            findings.append(f"{pid}: unresolved e2e result {result!r}")
        if row.get("config_fingerprint") != config_fingerprint(plugin):
            findings.append(f"{pid}: stale e2e evidence; auth/execution configuration changed")
        if result == "BLOCKED":
            if strict or pid not in LEGACY_PLUGIN_IDS:
                findings.append(f"{pid}: completed e2e PASS required for release/new built-in")
            blocker = row.get("blocker")
            if not isinstance(blocker, dict) or any(
                not isinstance(blocker.get(key), str) or len(blocker[key].strip()) < 12
                for key in ("reason", "owner", "next_action", "evidence")
            ):
                findings.append(
                    f"{pid}: BLOCKED needs concrete reason, owner, next_action and evidence"
                )
        mode = plugin.get("auth", {}).get("mode")
        exception = row.get("provider_exception")
        valid_exception = isinstance(exception, dict) and all(
            isinstance(exception.get(key), str) and len(exception[key].strip()) >= 12
            for key in ("reason", "source")
        )
        if result == "PASS" and mode not in BROWSER_AUTH_MODES and not valid_exception:
            findings.append(f"{pid}: non-browser PASS needs a documented provider_exception")
        evidence = row.get("evidence", {})
        if not isinstance(evidence, dict):
            findings.append(f"{pid}: evidence must be an object")
            continue
        for stage in E2E_STAGES:
            item = evidence.get(stage)
            if not isinstance(item, dict):
                findings.append(f"{pid}: missing {stage} evidence")
                continue
            status = item.get("status")
            detail = item.get("detail")
            reference = item.get("reference")
            if not isinstance(detail, str) or len(detail.strip()) < 12:
                findings.append(f"{pid}: {stage} needs observed detail")
            if not isinstance(reference, str) or not reference.startswith("docs/marketplace/"):
                findings.append(f"{pid}: {stage} needs a repository evidence reference")
            else:
                path = (ROOT / reference.split("#", 1)[0]).resolve()
                if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                    findings.append(f"{pid}: {stage} evidence reference does not exist")
            if status not in ("PASS", "BLOCKED", "NOT_APPLICABLE"):
                findings.append(f"{pid}: {stage} has unresolved status {status!r}")
            if result == "PASS" and status == "BLOCKED":
                findings.append(f"{pid}: PASS cannot have BLOCKED {stage}")
            if stage == "ui" and status != "PASS":
                findings.append(
                    f"{pid}: real UI attempt evidence must PASS even when externally blocked"
                )
            if status == "NOT_APPLICABLE" and not (
                (stage == "callback" and mode in ("oauth_device_flow", "local"))
                or (stage in ("browser", "callback") and valid_exception)
            ):
                findings.append(f"{pid}: {stage} cannot be declared NOT_APPLICABLE")
            if stage == "smoke" and status == "PASS" and item.get("real_action") is not True:
                findings.append(
                    f"{pid}: smoke must attest real_action=true; "
                    "tool discovery alone is insufficient"
                )
    return findings


PLACEHOLDER_MARKERS = (
    "replace_with",
    "your_client_id",
    "your-client-id",
    "changeme",
    "todo",
)


def is_placeholder(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    low = value.strip().lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def main() -> int:
    findings: list[str] = []
    try:
        seed = json.loads(SEED.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail closed with the reason
        print(f"auth-contract: cannot read seed catalog: {exc}")
        return 1
    plugins = seed.get("plugins", [])
    audit_src = AUDIT.read_text(encoding="utf-8") if AUDIT.exists() else ""
    try:
        e2e_audit = json.loads(E2E_AUDIT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(f"e2e: cannot read browser audit: {exc}")
    else:
        findings.extend(
            validate_e2e_audit(plugins, e2e_audit, strict="--require-e2e-pass" in sys.argv)
        )

    # Writable secret slots come from the wizard's SECRETS list (the same
    # source ALLOWED_SECRET_KEYS derives from). Import, don't grep: several
    # families are generated by comprehensions, so their literal key strings
    # never appear in the source text.
    try:
        sys.path.insert(0, str(ROOT))
        from jarvis.setup.wizard import SECRETS as _WIZARD_SECRETS

        secret_keys = {s.key for s in _WIZARD_SECRETS}
    except Exception as exc:  # noqa: BLE001 — fail closed with the reason
        print(f"auth-contract: cannot import wizard SECRETS: {exc}")
        return 1

    for pl in plugins:
        pid = str(pl.get("id", "?"))
        auth = pl.get("auth", {}) if isinstance(pl.get("auth", {}), dict) else {}
        mode = auth.get("mode", "?")

        # 1. No confidential secret values in tracked files.
        secret_val = auth.get("client_secret")
        if secret_val:
            findings.append(
                f"{pid}: catalog carries a client_secret value — "
                "confidential secrets never ship in the repo"
            )

        # 2. DCR discovery must be a usable absolute URL with a host.
        if mode == "hosted_mcp_oauth_dcr":
            disc = str(auth.get("discovery_url", ""))
            host = urlsplit(disc).hostname if disc.startswith("https://") else None
            if not host:
                findings.append(
                    f"{pid}: discovery_url has no host ({disc!r}) — DCR can never start"
                )

        # 3. Placeholder PKCE needs its expert-override family mapping.
        if mode == "oauth_pkce_loopback" and is_placeholder(auth.get("client_id")):
            family = pl.get("oauth_client_family")
            if not family:
                findings.append(f"{pid}: placeholder client_id with no oauth_client_family")
            else:
                # 4. Family needs BYO + publisher secret slots.
                for prefix in (f"{family}_oauth_client_", f"publisher_{family}_oauth_client_"):
                    for suffix in ("id", "secret"):
                        slot = f"{prefix}{suffix}"
                        if slot not in secret_keys:
                            findings.append(
                                f"{pid}: missing secret slot {slot} in jarvis/setup/wizard.py"
                            )

        # 3b. Device-flow placeholders resolve through the publisher client
        # the same way: without a family there is nowhere to provision it.
        if mode == "oauth_device_flow" and is_placeholder(auth.get("client_id")):
            if not pl.get("oauth_client_family"):
                findings.append(f"{pid}: placeholder device client_id with no oauth_client_family")

        # 6. PAT entries: https validation + documented prefix. Applies to
        # primary PAT blocks AND expert fallbacks on browser-primary plugins.
        pat_blocks = []
        if mode == "pat_paste":
            pat_blocks.append(("auth", auth))
        fallback = pl.get("fallback_auth")
        if isinstance(fallback, dict):
            if fallback.get("mode") != "pat_paste":
                findings.append(
                    f"{pid}: fallback_auth must use mode 'pat_paste' (got {fallback.get('mode')!r})"
                )
            elif mode == "pat_paste":
                findings.append(f"{pid}: fallback_auth is redundant on a pat_paste-primary plugin")
            else:
                pat_blocks.append(("fallback_auth", fallback))
        for label, block in pat_blocks:
            endpoint = str(block.get("validation_endpoint", ""))
            if endpoint and not endpoint.startswith("https://"):
                findings.append(f"{pid}: {label} validation_endpoint is not https ({endpoint!r})")
            if not block.get("token_prefix") and not block.get("token_prefixes"):
                # Empty prefix is allowed (provider validates), but the entry
                # must say so via an explicit empty string, not a missing key.
                if "token_prefix" not in block:
                    findings.append(f"{pid}: {label} without any token_prefix key")

        # Standard declaration fields: optional, but when present they must
        # use the documented vocabulary (browser-auth-standard.md §2).
        flow = pl.get("browser_flow")
        if flow is not None and flow not in (
            "dcr",
            "publisher_pkce",
            "device",
            "hosted",
            "local",
            "manual_token",
        ):
            findings.append(f"{pid}: unknown browser_flow {flow!r}")

        # 5. Every seed plugin id appears in the audit matrix.
        if pid not in audit_src:
            findings.append(f"{pid}: missing from docs/marketplace/plugin-auth-audit.md")

    # Mirrors: auth-relevant fields must match the seed 1:1 (modulo the
    # documented `-`/`_` renames, e.g. microsoft-todo vs microsoft_todo).
    if MIRRORS.is_dir():
        seed_by_id = {str(p.get("id", "")): p for p in plugins}

        def _seed_key(name: str) -> str:
            if name in seed_by_id:
                return name
            under = name.replace("-", "_")
            if under in seed_by_id:
                return under
            return name

        for mirror_dir in sorted(MIRRORS.iterdir()):
            manifest = mirror_dir / "plugin.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                findings.append(f"mirror {mirror_dir.name}: unreadable plugin.json ({exc})")
                continue
            ext = (data.get("extensions") or {}).get("io.github.personaljarvis", {})
            auth = ext.get("auth", {}) if isinstance(ext.get("auth"), dict) else {}
            if not auth:
                continue
            disc = str(auth.get("discovery_url", ""))
            if auth.get("mode") == "hosted_mcp_oauth_dcr" and disc:
                if not (disc.startswith("https://") and urlsplit(disc).hostname):
                    findings.append(f"mirror {mirror_dir.name}: hostless discovery_url ({disc!r})")
            seed_entry = seed_by_id.get(_seed_key(str(data.get("name", ""))), {})
            seed_auth = seed_entry.get("auth", {}) or {}
            if seed_auth and auth.get("mode") != seed_auth.get("mode"):
                findings.append(
                    f"mirror {mirror_dir.name}: mode {auth.get('mode')!r} "
                    f"drifts from seed mode {seed_auth.get('mode')!r}"
                )
            if seed_auth and auth.get("mode") == "hosted_mcp_oauth_dcr":
                if str(auth.get("discovery_url", "")) != str(seed_auth.get("discovery_url", "")):
                    findings.append(
                        f"mirror {mirror_dir.name}: discovery_url drifts from seed "
                        f"({auth.get('discovery_url')!r} vs {seed_auth.get('discovery_url')!r})"
                    )
            if seed_auth and auth.get("mode") == "oauth_pkce_loopback":
                if is_placeholder(auth.get("client_id")) != is_placeholder(
                    seed_auth.get("client_id")
                ):
                    findings.append(f"mirror {mirror_dir.name}: placeholder state drifts from seed")
            seed_fallback = seed_entry.get("fallback_auth")
            mirror_fallback = ext.get("fallback_auth")
            if (seed_fallback is None) != (mirror_fallback is None):
                findings.append(
                    f"mirror {mirror_dir.name}: fallback_auth presence drifts from seed"
                )
            elif isinstance(seed_fallback, dict) and isinstance(mirror_fallback, dict):
                if mirror_fallback.get("mode") != seed_fallback.get("mode"):
                    findings.append(
                        f"mirror {mirror_dir.name}: fallback_auth mode drifts from seed"
                    )

    # No invented-client guard: the repo must never contain a hardcoded
    # looking real client id where a placeholder belongs. (Publisher ids
    # travel via secrets/env, never via tracked files.)
    hexish = re.compile(r"^[A-Za-z0-9\-_]{16,}$")
    for pl in plugins:
        auth = pl.get("auth", {})
        if isinstance(auth, dict) and auth.get("mode") == "oauth_pkce_loopback":
            cid = str(auth.get("client_id", ""))
            if not is_placeholder(cid) and hexish.match(cid) and len(cid) >= 24:
                findings.append(
                    f"{pl.get('id')}: catalog client_id looks like a real shipped "
                    "secret-bearing id — publisher ids travel via secrets, not the repo"
                )

    if findings:
        print("auth-contract: FAILING findings:")
        for f in sorted(set(findings)):
            print(f"  - {f}")
        return 1
    print(f"auth-contract: clean ({len(plugins)} seed plugins checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
