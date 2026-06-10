"""Tests fuer Critic-Prompt-Templates.

Pruefen explizit die fuenf Design-Reviewer-Kriterien aus
`.claude/agents/jarvis-critic-design-reviewer.md`:
- Kriterium 1 — Evidence-Cite-Pflicht (Prompt verlangt file:line / log_line:N / test:name).
- Kriterium 2 — Adversarial Framing verbatim aus Research-Doc §F.
- Kriterium 3 — Anchor-Token (mission_prompt verbatim, triple-bracketed).
"""
from __future__ import annotations

from jarvis.missions.critic.prompts import (
    ADVERSARIAL_REFRAME_PREFIX,
    CRITIC_SYSTEM_PROMPT,
    render_critic_prompt,
)


# --- Kriterium 2 — Adversarial Framing ---


def test_template_contains_skeptical_phrasing() -> None:
    assert "skeptical of this implementation" in CRITIC_SYSTEM_PROMPT


def test_template_demands_minimum_three_issues() -> None:
    assert "find at least three concrete bugs" in CRITIC_SYSTEM_PROMPT


def test_template_demands_falsification_justification() -> None:
    assert (
        "explain why each plausible failure mode does NOT apply"
        in CRITIC_SYSTEM_PROMPT
    )


def test_template_uses_adversarial_role() -> None:
    assert "adversarial code critic" in CRITIC_SYSTEM_PROMPT


# --- Kriterium 1 — Evidence-Cite-Pflicht ---


def test_template_requires_evidence_format() -> None:
    assert "file:line" in CRITIC_SYSTEM_PROMPT
    assert "log_line:N" in CRITIC_SYSTEM_PROMPT
    assert "test:name" in CRITIC_SYSTEM_PROMPT


def test_template_rejects_empty_evidence_pass() -> None:
    assert "Empty-evidence PASSes are also rejected" in CRITIC_SYSTEM_PROMPT


def test_template_rejects_empty_evidence_fail() -> None:
    assert "Empty-evidence FAILs are treated as abstentions" in CRITIC_SYSTEM_PROMPT


# --- Kriterium 3 — Anchor-Token ---


def test_anchor_token_triple_bracketed_in_template() -> None:
    """Das Template enthaelt die Bracket-Marker — nicht den eingesetzten String."""
    assert "<<<{mission_prompt}>>>" in CRITIC_SYSTEM_PROMPT


def test_render_includes_mission_prompt_verbatim() -> None:
    user_request = "Schreibe eine Funktion is_palindrome(s: str) -> bool"
    out = render_critic_prompt(
        mission_prompt=user_request,
        worker_diff="diff --git ...",
        log_summary="log...",
        prior_reflections="",
        iteration=0,
    )
    assert f"<<<{user_request}>>>" in out


def test_render_does_not_paraphrase_mission_prompt() -> None:
    """Selbst bei langen Prompts mit Sonderzeichen verbatim einsetzen."""
    weird = "Build X with !@#$%^&*() in the name and \"quoted\" parts."
    out = render_critic_prompt(
        mission_prompt=weird,
        worker_diff="d",
        log_summary="l",
        prior_reflections="r",
        iteration=1,
    )
    assert weird in out


# --- Schema-Reminder + Output-Rules ---


def test_template_includes_output_schema_keys() -> None:
    for key in (
        "verdict",
        "axes",
        "issues",
        "correction_instruction",
        "summary",
        "confidence",
        "suggested_next_action",
    ):
        assert f'"{key}"' in CRITIC_SYSTEM_PROMPT


def test_template_demands_no_prose_no_markdown() -> None:
    """`no prose` + `markdown` koennen ueber einen Zeilenumbruch verteilt sein."""
    assert "no prose" in CRITIC_SYSTEM_PROMPT
    assert "markdown" in CRITIC_SYSTEM_PROMPT
    assert "no code fences" in CRITIC_SYSTEM_PROMPT


def test_template_states_read_only_mode() -> None:
    """Critic must be told not to modify files. As of 2026-05-15 the wording
    is honest about the fact that OpenClaw 2026.5.7 does NOT enforce this
    via a `--permission-mode plan` flag — it's a behavioural request, not a
    hard guarantee. Fix A+B (empty-diff pre-gate + hearsay rule) catch the
    sycophancy class this used to mask."""
    assert "Do NOT modify files" in CRITIC_SYSTEM_PROMPT
    # Either the old hard-claim or the new honest "advisory mode" phrasing.
    assert (
        "Read-only mode" in CRITIC_SYSTEM_PROMPT
        or "advisory mode" in CRITIC_SYSTEM_PROMPT
    )


def test_template_has_ground_truth_rule() -> None:
    """Fix B regression: empty-diff must veto regardless of log claims.
    Without this rule the Critic was sycophantic to worker-text claims like
    'file successfully created' (live repro mission_019e2c18, 2026-05-15)."""
    assert "GROUND-TRUTH-RULE" in CRITIC_SYSTEM_PROMPT
    assert "diff is ground truth" in CRITIC_SYSTEM_PROMPT
    assert "log is hearsay" in CRITIC_SYSTEM_PROMPT


def test_template_has_meta_phrase_rule() -> None:
    """Live false-positive mission_019eb1ac (2026-06-10): the user asked Jarvis
    to "spawn a subagent that creates an HTML file". The worker produced a
    substantial HTML file, but the Critic treated the routing meta-instruction
    ("spawn a subagent") as part of the deliverable and returned verdict=revise
    demanding evidence that an agent was actually spawned. The mission runtime
    IS the spawned subagent; such phrases are routing meta-language, never a
    deliverable. The Critic must never demand agent/subagent-spawning evidence
    nor fail an axis for missing it."""
    assert "META-PHRASE-RULE" in CRITIC_SYSTEM_PROMPT
    assert "spawn a subagent" in CRITIC_SYSTEM_PROMPT
    assert "The mission runtime IS that subagent" in CRITIC_SYSTEM_PROMPT
    assert (
        "Never demand evidence that an agent" in CRITIC_SYSTEM_PROMPT
        or "never demand evidence that an agent" in CRITIC_SYSTEM_PROMPT
    )


def test_ground_truth_rule_recognizes_verified_external_writes() -> None:
    """mission_019e7abd (2026-05-30): a worker may legitimately write to an
    absolute path OUTSIDE the worktree (e.g. the user's Desktop). The
    Kontrollierer surfaces such deliverables as `diff --external-target` blocks
    that are verified on disk. The GROUND-TRUTH-RULE must treat those as real
    delivered content — NOT as an empty diff to veto — otherwise out-of-worktree
    tasks fail 3× even when the file exists and is correct."""
    assert "diff --external-target" in CRITIC_SYSTEM_PROMPT
    # The empty-diff veto must explicitly carve out the verified-external case.
    assert "verified-external-write" in CRITIC_SYSTEM_PROMPT


# --- Render-Behavior ---


def test_render_includes_diff_log_reflections_iteration() -> None:
    out = render_critic_prompt(
        mission_prompt="P",
        worker_diff="MY_DIFF_TOKEN",
        log_summary="MY_LOG_TOKEN",
        prior_reflections="MY_REFL_TOKEN",
        iteration=2,
    )
    assert "MY_DIFF_TOKEN" in out
    assert "MY_LOG_TOKEN" in out
    assert "MY_REFL_TOKEN" in out
    assert "CURRENT ITERATION: 2" in out


def test_render_handles_empty_reflections() -> None:
    out = render_critic_prompt(
        mission_prompt="p",
        worker_diff="d",
        log_summary="l",
        prior_reflections="",
        iteration=0,
    )
    assert "<<<>>>" in out  # explizit empty Anchor-Bracket


def test_render_adversarial_reframe_prepends_prefix() -> None:
    plain = render_critic_prompt(
        mission_prompt="p",
        worker_diff="d",
        log_summary="l",
        prior_reflections="r",
        iteration=0,
        adversarial_reframe=False,
    )
    reframed = render_critic_prompt(
        mission_prompt="p",
        worker_diff="d",
        log_summary="l",
        prior_reflections="r",
        iteration=0,
        adversarial_reframe=True,
    )
    assert reframed.startswith(ADVERSARIAL_REFRAME_PREFIX)
    assert plain in reframed
    assert ADVERSARIAL_REFRAME_PREFIX not in plain


def test_adversarial_prefix_emphasizes_skepticism() -> None:
    assert "sycophantic critic" in ADVERSARIAL_REFRAME_PREFIX
    assert "default position is now FAIL" in ADVERSARIAL_REFRAME_PREFIX


def test_render_anchor_present_in_both_modes() -> None:
    """Anchor-Token muss auch im Adversarial-Reframe-Modus drin sein."""
    out = render_critic_prompt(
        mission_prompt="ANCHOR_X",
        worker_diff="d",
        log_summary="l",
        prior_reflections="r",
        iteration=0,
        adversarial_reframe=True,
    )
    assert "<<<ANCHOR_X>>>" in out
