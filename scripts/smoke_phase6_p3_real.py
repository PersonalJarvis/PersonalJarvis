"""Smoke test phase 3 — REAL critic call (costs ~$0.20).

End-to-end test with a real `openclaw agent --json-schema ...` subprocess.
Skips cleanly when the claude CLI is not installed/authenticated
(same pattern as smoke_phase6_p2.py). Exit 0 on success OR SKIP,
exit 1 only on a real failure.

What gets verified:
1. The claude CLI is installed and authenticated (otherwise SKIP).
2. CriticRunner.run() spawns a real claude subprocess with:
   - --output-format json
   - --json-schema <CRITIC_JSON_SCHEMA>
   - --model sonnet
   - --max-turns 1
   - --permission-mode plan
   - [--bare] when ANTHROPIC_API_KEY is in env
3. The critic returns a schema-valid CriticVerdict.
4. The verdict text contains the anchor token (mission_prompt verbatim).

Run:
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


# --- Mini mission ---

MISSION_PROMPT = (
    "Write a Python function `is_palindrome(s: str) -> bool` "
    "that ignores whitespace and case. Include doctests."
)

WORKER_DIFF = """\
diff --git a/palindrome.py b/palindrome.py
new file mode 100644
+++ b/palindrome.py
@@ -0,0 +1,5 @@
+def is_palindrome(s: str) -> bool:
+    \"\"\"Returns True if s is a palindrome.\"\"\"
+    cleaned = ''.join(c.lower() for c in s if not c.isspace())
+    return cleaned == cleaned[::-1]
"""

WORKER_LOG = """\
[claude] starting iteration 0
[claude] writing palindrome.py
[claude] result: success
"""


def claude_available() -> bool:
    """Quick existence check for the claude CLI."""
    return shutil.which("claude") is not None


async def smoke_real() -> int:
    if not claude_available():
        print(f"{SKIP} claude CLI not in PATH — real smoke test skipped.")
        print(f"      Install: https://docs.claude.com/en/docs/openclaw")
        return 0

    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_api_key:
        print(
            f"{SKIP} no ANTHROPIC_API_KEY — real smoke test needs either "
            "the key (for the --bare path) or an existing OAuth session via "
            "`claude /login`. With OAuth: test runs without --bare. Continuing..."
        )

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "wt"
        worktree.mkdir()

        env = dict(os.environ)  # inherits usable auth (OAuth tokens, keychain refs)

        runner = CriticRunner(timeout_seconds=120.0)

        print(f"{OK} spawning real critic ({'--bare' if has_api_key else 'OAuth path'}) ...")
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

        # Validations
        print(f"{OK} verdict received: {verdict.verdict}, confidence={verdict.confidence:.2f}")
        if verdict.verdict not in ("approve", "revise", "reject"):
            print(f"{FAIL} unknown verdict {verdict.verdict!r}")
            return 1
        if not verdict.summary:
            print(f"{FAIL} verdict has empty summary")
            return 1
        if not verdict.summary_de:
            print(f"{FAIL} verdict has empty summary_de — TTS path would be broken")
            return 1
        print(f"{OK} verdict has summary + summary_de")
        print(f"      summary: {verdict.summary[:120]}")
        print(f"      summary_de: {verdict.summary_de[:120]}")
        print(f"      issues: {len(verdict.issues)}, suggested_action: {verdict.suggested_next_action}")

    print()
    print(f"{OK} REAL SMOKE GREEN — critic loop is production-ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke_real()))
