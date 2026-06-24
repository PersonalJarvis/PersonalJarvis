"""Tests for the worker-prompt composer + the artifact-language directive.

Root cause (2026-06-22): nothing in the mission-dispatch chain ever told the
worker to produce English code artifacts. The mission prompt is mostly German
(the user speaks German, ``spawn_worker`` wraps it with German labels), so the
worker naturally wrote German identifiers/comments/docstrings — violating the
repo Output Language Policy ("every artifact an agent produces is English").

The fix injects ``ARTIFACT_LANGUAGE_DIRECTIVE`` at the single guaranteed
chokepoint (``orchestrator._run_iterations`` builds the ``worker_prompt`` for
every worker, every iteration, every decomposition path) via
``compose_worker_prompt``. These tests pin that composer's contract.
"""
from __future__ import annotations

from jarvis.missions.kontrollierer.worker_prompt import (
    ARTIFACT_LANGUAGE_DIRECTIVE,
    OUTPUT_SHAPE_DIRECTIVE,
    compose_worker_prompt,
)

# --- The directive itself -----------------------------------------------------


def test_directive_mandates_english_for_code_artifacts() -> None:
    text = ARTIFACT_LANGUAGE_DIRECTIVE.lower()
    assert "english" in text
    # It must name the code-artifact surfaces it governs, not just "be English".
    assert "comment" in text
    assert "identifier" in text or "docstring" in text


def test_directive_is_language_independent_not_a_de_en_toggle() -> None:
    # The leak was: German request -> German code. The directive must hold
    # REGARDLESS of the request language, so it cannot be a "de/en" branch.
    text = ARTIFACT_LANGUAGE_DIRECTIVE.lower()
    assert "even" in text or "regardless" in text


def test_directive_preserves_user_facing_content_exception() -> None:
    # The user explicitly said: sometimes German is correct. A generated
    # German news page or quoted text must stay German — only code artifacts
    # default to English.
    text = ARTIFACT_LANGUAGE_DIRECTIVE.lower()
    assert "user-facing" in text or "content" in text
    assert "explicit" in text or "request" in text


# --- OUTPUT_SHAPE_DIRECTIVE ---------------------------------------------------
# Root cause (2026-06-22, mission 019ef052): the user asked for a SINGLE HTML
# file; the worker shipped four (index.html + app.js + styles.css + assets/).
# The standing _QUALITY_DIRECTIVE ("never downgrade to a minimal version,
# skeleton is a floor not a ceiling") reads a single self-contained file as a
# forbidden minimal version, so the worker built the "production-quality" norm
# of a split multi-file project. Nothing in the chain told the worker to honor
# an explicit form/shape constraint. This directive is that missing layer.


def test_shape_directive_honors_explicit_single_file_constraint() -> None:
    text = OUTPUT_SHAPE_DIRECTIVE.lower()
    # The canonical case that broke: "a single (HTML) file".
    assert "single" in text
    assert "file" in text
    # It must frame the requested shape as a requirement to follow exactly,
    # not an optional preference.
    assert "honor" in text or "honour" in text or "exactly" in text


def test_shape_directive_says_honoring_a_constraint_is_not_a_downgrade() -> None:
    # The whole point: neutralize the _QUALITY_DIRECTIVE's "never downgrade to
    # minimal" pull, which otherwise treats a single-file deliverable as a
    # forbidden minimal/stub version.
    text = OUTPUT_SHAPE_DIRECTIVE.lower()
    assert "downgrade" in text
    assert "stub" in text or "minimal" in text


def test_shape_directive_is_a_default_keeping_judgment_when_unconstrained() -> None:
    # No explicit shape constraint -> the worker keeps professional judgment for
    # packaging; the directive must NOT force everything into one file always.
    text = OUTPUT_SHAPE_DIRECTIVE.lower()
    assert "judgment" in text or "judgement" in text


def test_shape_directive_is_provider_and_language_agnostic() -> None:
    # Like the language directive, it must hold regardless of request language /
    # which worker CLI runs — no de/en toggle, no provider name.
    text = OUTPUT_SHAPE_DIRECTIVE.lower()
    for banned in ("claude", "gemini", "codex", "grok", "anthropic", "openai"):
        assert banned not in text


def test_compose_includes_output_shape_directive() -> None:
    out = compose_worker_prompt("", "Make a single HTML file about moving to SF")
    assert OUTPUT_SHAPE_DIRECTIVE in out


def test_compose_orders_both_directives_before_the_step() -> None:
    step = "Mach mir eine einzige, in sich geschlossene HTML-Datei."
    out = compose_worker_prompt("", step)
    assert OUTPUT_SHAPE_DIRECTIVE in out
    assert ARTIFACT_LANGUAGE_DIRECTIVE in out
    assert out.index(OUTPUT_SHAPE_DIRECTIVE) < out.index(step)
    assert out.index(ARTIFACT_LANGUAGE_DIRECTIVE) < out.index(step)


# --- compose_worker_prompt ----------------------------------------------------


def test_compose_puts_directive_before_the_step_prompt() -> None:
    step = "Erstelle ein sinnvolles HTML-Grundgeruest."
    out = compose_worker_prompt("", step)
    assert ARTIFACT_LANGUAGE_DIRECTIVE in out
    assert step in out
    assert out.index(ARTIFACT_LANGUAGE_DIRECTIVE) < out.index(step)


def test_compose_keeps_prior_reflections_and_orders_directive_first() -> None:
    prior = "PRIOR CRITIC FEEDBACK: the previous attempt left a stub."
    step = "Finish the deliverable."
    out = compose_worker_prompt(prior, step)
    # All three present, directive first, step last.
    assert ARTIFACT_LANGUAGE_DIRECTIVE in out
    assert prior in out
    assert step in out
    assert out.index(ARTIFACT_LANGUAGE_DIRECTIVE) < out.index(prior) < out.index(step)


def test_compose_without_prior_block_has_no_dangling_separator() -> None:
    out = compose_worker_prompt("", "do the thing")
    assert "\n\n\n" not in out
    assert out.strip() == out
