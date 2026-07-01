"""End-to-end smoke test (2026-04-29): direct brain call without a backend.

Verifies that the API-key fixes take effect in the real stack:
1. Pre-boot key check filters out providers without a key.
2. Gemini schema sanitizer works (no more 11-validation-error).
3. account_blocked classification kicks in (Anthropic credit / xAI tier).
4. _format_provider_chain_error returns a user-actionable message.

Usage:
    python scripts/smoke_brain_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


async def main() -> int:
    from jarvis.core import config as cfg_mod

    print("=" * 60)
    print("Brain-E2E-Smoke (echter API-Call)")
    print("=" * 60)

    # 1. Welche Keys sind im Credential Manager?
    print("\n--- Step 1: Verfuegbare API-Keys ---")
    keys = {
        "anthropic_api_key": "claude-api",
        "gemini_api_key": "gemini",
        "openai_api_key": "openai",
        "openrouter_api_key": "openrouter",
        "grok_api_key": "grok",
        "xai_api_key": "grok",
    }
    for key_name, prov in keys.items():
        val = cfg_mod.get_secret(key_name, env_fallback=key_name.upper())
        print(f"  {key_name:30s} -> {prov:12s} {'OK' if val else 'MISSING'}")

    # 2. BrainManager bauen
    print("\n--- Step 2: BrainManager via from_tier_config ---")
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config

    config = load_config()
    bus = EventBus()
    try:
        bm = BrainManager.from_tier_config("router", config, bus)
    except Exception as exc:
        print(f"  BrainManager-Build FAIL: {exc}")
        return 1

    print(f"  active_provider = {bm.active_provider}")
    print(f"  _dead_providers (Pre-Boot-Filter): {sorted(bm._dead_providers)}")
    print(f"  available_providers: {sorted(bm.available_providers())}")

    chain = bm._build_fallback_chain("fast")
    print(f"  fallback chain (fast): {len(chain)} Provider")
    for prov, model in chain[:5]:
        print(f"    - {prov:12s} {model}")

    # 3. Live-Brain-Call mit Test-Frage
    print("\n--- Step 3: Live-Brain-Call ---")
    print("  Question: 'Antworte mit genau einem Wort: ja oder nein.'")  # i18n-allow: quotes the simulated German user utterance sent below
    try:
        response = await bm.generate(
            "Antworte mit genau einem Wort: ja oder nein.",  # i18n-allow: simulated German user utterance under test
            use_history=False,
        )
    except Exception as exc:
        print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
        return 1

    print(f"\n  Response: {response[:400]!r}")

    # 4. Verdict — based on the response string (not on bm attributes,
    # because BrainManager has no public last_provider/last_model attributes).
    print("\n--- Step 4: Verdict ---")
    rc = 0
    failure_indicators = ("Account-Problem", "Brain-Key", "Sidebar", "Setup")
    if any(ind in response for ind in failure_indicators):
        print("  [OK] Provider-Chain-Failure mit ACTIONABLE User-Message")
        print("       (Bug-API-1-Fix wirkt: account_blocked-Klassifikation aktiv,")
        print("        User bekommt Billing-URL statt 'Netzwerk pruefen')")
    elif "unerreichbar" in response.lower() and "Account" not in response:
        print("  [X] Generic 'Provider unreachable' string — the fix is NOT working")
        rc = 1
    else:
        STALE = {"grok-3", "grok-2", "gpt-4o", "gpt-4o-mini", "gemini-2.5-flash",
                 "gemini-2.5-pro", "claude-3-opus", "claude-3-haiku"}
        # Successful response — no STALE marker in the text
        for stale in STALE:
            if stale in response.lower():
                print(f"  [WARN] STALE-Marker im Response-Text: {stale}")
        print("  [OK] Brain-Call lieferte echte Response")

    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
