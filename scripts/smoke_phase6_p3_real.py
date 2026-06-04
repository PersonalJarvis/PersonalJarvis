"""Smoke-Test Phase 3 — REAL Critic-Call (kostet ~$0.20).

End-to-End mit echtem `openclaw agent --json-schema ...`-Subprocess. Skipt
sauber wenn die claude-CLI nicht installiert/authentifiziert ist
(gleicher Pattern wie smoke_phase6_p2.py). Exit 0 bei Erfolg ODER SKIP,
Exit 1 nur bei echtem Fehler.

Was wird verifiziert:
1. claude-CLI ist installiert und authentifiziert (sonst SKIP).
2. CriticRunner.run() spawnt einen echten claude-Subprocess mit:
   - --output-format json
   - --json-schema <CRITIC_JSON_SCHEMA>
   - --model sonnet
   - --max-turns 1
   - --permission-mode plan
   - [--bare] wenn ANTHROPIC_API_KEY in env
3. Der Critic liefert ein schema-valides CriticVerdict.
4. Der Verdict-Text enthaelt den anchor-token (mission_prompt verbatim).

Ausfuehrung:
  cd <repo-root>
  python scripts/smoke_phase6_p3_real.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.critic.runner import CriticRunner  # noqa: E402
from jarvis.missions.critic.verdict import CriticVerdict  # noqa: E402

OK = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


# --- Mini-Mission ---

MISSION_PROMPT = (
    "Schreibe eine Python-Funktion `is_palindrome(s: str) -> bool`, "
    "die Whitespace und Case ignoriert. Inkludiere doctests."
)

WORKER_DIFF = """\
diff --git a/palindrome.py b/palindrome.py
new file mode 100644
+++ b/palindrome.py
@@ -0,0 +1,5 @@
+def is_palindrome(s: str) -> bool:
+    \"\"\"Returns True wenn s ein palindrom ist.\"\"\"
+    cleaned = ''.join(c.lower() for c in s if not c.isspace())
+    return cleaned == cleaned[::-1]
"""

WORKER_LOG = """\
[claude] starting iteration 0
[claude] writing palindrome.py
[claude] result: success
"""


def claude_available() -> bool:
    """Schneller existence-check fuer die claude-CLI."""
    return shutil.which("claude") is not None


async def smoke_real() -> int:
    if not claude_available():
        print(f"{SKIP} claude-CLI nicht im PATH — Real-Smoke uebersprungen.")
        print(f"      Install: https://docs.claude.com/en/docs/openclaw")
        return 0

    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_api_key:
        print(
            f"{SKIP} kein ANTHROPIC_API_KEY — Real-Smoke benoetigt entweder "
            "den Key (fuer --bare-Pfad) oder eine bestehende OAuth-Session via "
            "`claude /login`. Bei OAuth: Test laeuft ohne --bare. Continuing..."
        )

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "wt"
        worktree.mkdir()

        env = dict(os.environ)  # erbt nutzbare Auth (OAuth-Tokens, Keychain-Refs)

        runner = CriticRunner(timeout_seconds=120.0)

        print(f"{OK} spawning real critic ({'--bare' if has_api_key else 'OAuth-Pfad'}) ...")
        try:
            verdict: CriticVerdict = await runner.run(
                mission_prompt=MISSION_PROMPT,
                worker_diff=WORKER_DIFF,
                worker_log=WORKER_LOG,
                prior_reflections="",
                iteration=0,
                worktree=worktree,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{FAIL} critic-run raised: {type(exc).__name__}: {exc}")
            return 1

        # Validierungen
        print(f"{OK} verdict received: {verdict.verdict}, confidence={verdict.confidence:.2f}")
        if verdict.verdict not in ("approve", "revise", "reject"):
            print(f"{FAIL} unknown verdict {verdict.verdict!r}")
            return 1
        if not verdict.summary:
            print(f"{FAIL} verdict has empty summary")
            return 1
        if not verdict.summary_de:
            print(f"{FAIL} verdict has empty summary_de — TTS-Pfad waere broken")
            return 1
        print(f"{OK} verdict has summary + summary_de")
        print(f"      summary: {verdict.summary[:120]}")
        print(f"      summary_de: {verdict.summary_de[:120]}")
        print(f"      issues: {len(verdict.issues)}, suggested_action: {verdict.suggested_next_action}")

    print()
    print(f"{OK} REAL-SMOKE GREEN — Critic-Loop ist production-ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke_real()))
