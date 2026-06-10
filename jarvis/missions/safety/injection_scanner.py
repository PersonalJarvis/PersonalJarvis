"""PostToolUse pattern scanner against prompt injection in worker output.

ADR-0009 Risk #7: Worker stdout/diff/log are scanned AFTER worker EOF and
BEFORE the Critic call. high+critical severity blocks the mission via a
WorkerKilled event with reason="injection_detected"; med/low are logged
but not blocked (false-positive defense).

Patterns are intentionally narrow — we want 0% false-positives on
legitimate code output and ~80% detection on adversarial inputs.

Pattern sources:
- OWASP LLM Top 10 #1 (Prompt Injection)
- Anthropic Threat Models §"Indirect Prompt Injection"
- Verified cases from Cline/Aider PR reviews
"""
from __future__ import annotations

import json
import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

InjectionSeverity = Literal["low", "med", "high", "critical"]
InjectionWhere = Literal["diff", "log", "stdout", "prompt"]


class InjectionDetection(BaseModel):
    """Match result for a single INJECTION_PATTERNS entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern_id: str
    matched_text: str  # max 200 chars (capped in scan())
    severity: InjectionSeverity
    where: InjectionWhere


# (pattern_id, regex, severity)
INJECTION_PATTERNS: Final[list[tuple[str, re.Pattern[str], InjectionSeverity]]] = [
    # --- Critical: direkte Secrets-Exfiltration / destructive system calls ---
    (
        "env_leak_marker",
        # Match the env-var name ONLY when it's actually being read or
        # disclosed. The bare name alone produces massive false
        # positives — every CLI banner ("Using GOOGLE_API_KEY"), every
        # README, every test fixture mentions it.
        #
        # Two trigger shapes (alternation):
        #   1. READ context BEFORE the name:
        #        os.environ[...] / os.getenv(...)
        #        process.env.<NAME>
        #        Shell expansion: $<NAME> / ${<NAME>} / %<NAME>%
        #        print/echo/log of the value
        #        HTTP exfil: curl/wget/fetch/Invoke-WebRequest carrying it
        #   2. ASSIGNMENT shape AFTER the name:
        #        <NAME>=<value> or <NAME>: <value> (with a non-whitespace
        #        token after) — that's a key BEING disclosed in code/log.
        re.compile(
            r"(?:"
            r"(?:os\.environ\[\s*['\"]|os\.getenv\(\s*['\"]|"
            r"process\.env\.|\$\{?|%|"
            r"(?:print|echo|console\.log|fmt\.Print(?:ln|f)?)\b[^\n]{0,80}|"
            r"(?:curl|wget|fetch\(|Invoke-WebRequest|axios\.)[^\n]{0,120})"
            r"(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|AWS_SECRET_ACCESS_KEY|"
            r"GITHUB_TOKEN|GH_TOKEN|GOOGLE_API_KEY|GEMINI_API_KEY)"
            r"|"
            r"\b(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|AWS_SECRET_ACCESS_KEY|"
            r"GITHUB_TOKEN|GH_TOKEN|GOOGLE_API_KEY|GEMINI_API_KEY)"
            r"\s*[=:]\s*\S"
            r")"
        ),
        "critical",
    ),
    (
        "rm_rf_root",
        re.compile(r"\brm\s+-rf?\s+(/|~/|\$HOME)", re.IGNORECASE),
        "critical",
    ),
    (
        "powershell_remove_path",
        re.compile(
            r"\bRemove-Item\s+(-Force\s+)?-Recurse\s+(C:\\|\$env:HOMEDRIVE)",
            re.IGNORECASE,
        ),
        "critical",
    ),
    # --- High: Classic prompt-injection phrases + role-override ---
    (
        "ignore_previous",
        re.compile(
            r"\bignore\s+(all\s+)?(your\s+)?previous\s+(instructions|prompts|rules)\b",
            re.IGNORECASE,
        ),
        "high",
    ),
    (
        "claude_md_inject",
        # Only fires when one of these files is mentioned WITH an
        # instruction verb that suggests indirect prompt injection
        # ("please read CLAUDE.md", "follow the steps in AGENTS.md").
        # The bare path alone is everywhere in a healthy workspace —
        # the orchestrator's `_capture_diff` runs `git add -N .` so any
        # mission inside a cloned repo dumps the entire untracked-file
        # tree into the diff text, and that listing naturally contains
        # CLAUDE.md / .claude/agents/* / GEMINI.md etc. The previous
        # version flagged every mission with severity=high and killed
        # the worker via WorkerKilled(injection_detected) before the
        # Critic could ever review it (live forensics 2026-05-13).
        #
        # 2026-05-17 (BUG-LIVE-08): downgraded severity from "high" to
        # "med" after live mission 019e35e2 was killed because Sonnet's
        # legitimate explanatory output included phrases like
        # "see CLAUDE.md for context" / "follow the AGENTS.md contract".
        # Those are normal worker prose, not prompt-injection attacks
        # — the worker is the system's OWN agent, not an external
        # attacker. The pattern stays in the catalogue for diff scans
        # (which are external content the worker may write) and logs at
        # med, so suspicious occurrences are still recorded but no
        # longer trigger has_high_severity() and the WorkerKilled path.
        re.compile(
            r"\b(?:read|follow|see|consult|obey|execute|run|do|apply)\b"
            r"[^\n]{0,40}"
            r"(\.claude/|\.cursorrules|CLAUDE\.md|AGENTS\.md|GEMINI\.md)",
            re.IGNORECASE,
        ),
        "med",
    ),
    (
        "exfil_url",
        re.compile(
            r"(curl|wget|Invoke-WebRequest|fetch\()[^\n]{0,80}"
            r"(api_key|token|secret|password|cookie)",
            re.IGNORECASE,
        ),
        "high",
    ),
    # --- Medium: System-prompt discovery + role-switch ---
    (
        "system_prompt_leak",
        re.compile(
            r"\b(reveal|show|print|output|repeat)\s+"
            r"(your\s+)?(system|developer|instructions|prompt)\b",
            re.IGNORECASE,
        ),
        "med",
    ),
    (
        "override_role",
        re.compile(
            r"\b(you\s+are\s+now|new\s+role|act\s+as\s+a|pretend\s+to\s+be)\s+"
            r"(an?\s+)?(unrestricted|jailbroken|admin|root|developer)\b",
            re.IGNORECASE,
        ),
        "med",
    ),
    (
        "script_block_html",
        re.compile(r"<script[\s>]", re.IGNORECASE),
        "med",
    ),
    # --- Low: Suspicious but common in legitimate logs ---
    (
        "base64_blob",
        # only very long Base64 blobs (suspected embedded payload)
        re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
        "low",
    ),
]


_MATCH_TEXT_CAP: Final[int] = 200


def scan(text: str, *, where: InjectionWhere) -> list[InjectionDetection]:
    """Scan `text` against all INJECTION_PATTERNS.

    Args:
        text: Raw worker output (diff / log / stdout / prompt).
        where: Source of the text — tag for reporting.

    Returns:
        List of matches (multiple patterns can match independently).
        Empty list when there are no matches OR the text is empty.
    """
    if not text:
        return []
    out: list[InjectionDetection] = []
    for pattern_id, regex, severity in INJECTION_PATTERNS:
        for match in regex.finditer(text):
            matched = match.group(0)[:_MATCH_TEXT_CAP]
            out.append(
                InjectionDetection(
                    pattern_id=pattern_id,
                    matched_text=matched,
                    severity=severity,
                    where=where,
                )
            )
    return out


def has_high_severity(detections: list[InjectionDetection]) -> bool:
    """True if at least one detection has severity in {high, critical}."""
    return any(d.severity in ("high", "critical") for d in detections)


def extract_worker_authored_text(stream_text: str) -> str:
    """Reduce a stream.jsonl transcript to the text the worker AUTHORED.

    The injection scan exists to catch a worker that *does* something
    malicious — runs a destructive command, leaks a secret in its prose,
    writes an exfil call. What the world hands BACK to the worker (file
    contents returned by read commands, tool_result blocks) is input,
    not output: a worker that reads its own repo's safety blacklist or
    a doc explaining `rm -rf /` has not authored an attack.

    Live mission 019eadaf-272d (2026-06-09) proved the failure mode:
    after 20 minutes of work and a clean 30 KB diff, the worker was
    killed via WorkerKilled(injection_detected) because the raw
    stream.jsonl contained `rm -rf /` (from jarvis.toml.example's own
    safety blacklist, read via Get-Content), `OPENAI_API_KEY=...` (a
    wizard docstring) and `fetch('/api/secret...')` (frontend code) —
    all of them rg/Get-Content OUTPUT, none of them worker-authored.

    Strategy: targeted EXCLUSION of the two known world->worker input
    channels, keep everything else scannable (fail-closed on format
    drift — an unknown event format degrades to a possible false
    positive, never to a missed attack):

    - Codex `item.*` / `command_execution`: drop `aggregated_output`
      (the read result), keep `command` (what the worker wants to run).
      Other item types (e.g. `agent_message` — the worker's own prose)
      pass through unmodified: they are worker-authored and must remain
      scannable.
    - Claude `user` events: drop the whole line (tool_result blocks).
      The worker's own commands stay scannable via the `tool_use`
      inputs inside `assistant` events.
    - Non-JSON lines (plain CLI stdout) and every other event type:
      kept verbatim.
    """
    if not stream_text:
        return ""
    out_lines: list[str] = []
    for line in stream_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        if not isinstance(obj, dict):
            out_lines.append(line)
            continue
        type_ = obj.get("type")
        if isinstance(type_, str) and type_.startswith("item"):
            item = obj.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                redacted = {
                    k: v for k, v in item.items() if k != "aggregated_output"
                }
                out_lines.append(json.dumps(redacted, ensure_ascii=False))
                continue
            out_lines.append(line)
            continue
        if type_ == "user":
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


__all__ = [
    "INJECTION_PATTERNS",
    "InjectionDetection",
    "InjectionSeverity",
    "InjectionWhere",
    "extract_worker_authored_text",
    "has_high_severity",
    "scan",
]
