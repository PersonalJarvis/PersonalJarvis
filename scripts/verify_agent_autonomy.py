"""Verify autonomous work contracts without contacting a billed provider."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402

TESTS = [
    "tests/unit/agent_runtimes/test_runtime_configs.py",
    "tests/contract/test_agent_autonomy.py",
    "tests/contract/test_routine_chats.py",
    "tests/contract/test_calendar_routines.py",
    "tests/unit/society/test_cli_routines.py",
    "tests/unit/society/test_proposals.py",
    "tests/unit/society/test_routines.py",
    "tests/unit/agent_chat/test_turn_completion.py",
    "tests/unit/tasks/test_scheduler.py",
    "tests/unit/tasks/test_scheduler_automations.py",
    "tests/unit/tasks/test_runner_agent_result.py",
    "tests/unit/brain/test_routing.py",
    "tests/unit/brain/test_output_filter.py",
    "tests/unit/sessions/test_hangup_reason_parity.py",
    "tests/unit/core/test_turn_language.py",
]
CONTRACTS = (
    "test_one_time_commitment_is_durable_without_approval_card",
    "test_inferred_recurring_goal_is_active_without_routine_keyword",
    "test_duplicate_delivery_and_restart_keep_id_and_original_deadline",
    "test_pause_resume_update_to_one_time_and_cancel_preserve_identity",
    "test_non_actions_and_explicit_exclusions_do_not_create_jobs",
    "test_forged_background_and_read_only_requests_are_refused",
    "test_interrupted_effects_are_not_replayed_at_restart",
    "test_paid_owner_waits_without_starting_a_turn_or_fallback",
    "test_runtime_run_reports_its_result_in_the_origin_chat",
    "test_unregistered_promise_continues_then_fails_honestly",
    "test_promise_needs_a_real_active_receipt_not_an_unrelated_tool",
    "test_delete_retains_cancellation_receipt",
    "test_concurrent_identical_calls_register_only_one_job",
    "test_waiting_recurring_task_is_not_restarted_by_a_stale_heap_entry",
    "test_permission_ceiling_prevents_autonomous_creation",
    "test_provider_error_does_not_fall_back_or_claim_completion",
)
SOURCES = [
    "jarvis/agent_runtimes/openclaw.py", "tests/unit/agent_runtimes/test_runtime_configs.py",
    "jarvis/agent_chat/background_work.py",
    "jarvis/agent_chat/turn_completion.py",
    "jarvis/society/autonomous_routines.py",
    "jarvis/society/agent_tools.py",
    "jarvis/society/proposals.py",
    "jarvis/society/routines.py",
    "jarvis/society/routine_runner.py",
    "jarvis/society/surface.py",
    "jarvis/tasks/runner.py",
    "jarvis/tasks/scheduler.py",
    "tests/contract/test_agent_autonomy.py",
    "scripts/verify_agent_autonomy.py",
]


def run(arguments: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
    return result.returncode


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if run(["-m", "ruff", "check", *SOURCES]):
        return 1
    with tempfile.TemporaryDirectory(prefix="jarvis-autonomy-proof-") as temporary:
        report = Path(temporary) / "tests.xml"
        if run(["-m", "pytest", *TESTS, "-q", "--tb=short", f"--junitxml={report}"]):
            return 1
        # This file was produced by our own pytest process in a fresh private directory.
        cases = list(ET.parse(report).iter("testcase"))  # noqa: S314
        for runtime in ("jarvis", "hermes", "openclaw"):
            for contract in CONTRACTS:
                matched = [
                    c for c in cases if c.get("name", "").startswith(f"{contract}[{runtime}")
                ]
                if not matched or any(len(case) for case in matched):
                    print(f"Missing, skipped or failed contract: {contract}[{runtime}]")
                    return 1
        print(f"Verified {len(cases)} tests; all three runtime contract matrices passed.")
    print("Evidence: real MCP gates, SQLite and scheduler; scripted model/CLI transport.")
    print(
        "Installed providers, native runtime processes and active desktop are not certified here."
    )
    print("AGENT_AUTONOMY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
