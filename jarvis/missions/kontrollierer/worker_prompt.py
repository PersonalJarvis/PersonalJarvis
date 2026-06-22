"""Worker-prompt composition + the artifact-language directive.

This is the single chokepoint that guarantees every dispatched worker is told
to produce **English code artifacts by default**, regardless of the language the
user spoke in.

Why a dedicated module
----------------------
The mission-dispatch chain is mostly German-flavoured: the user speaks German,
``spawn_worker._build_mission_prompt`` wraps the request with German labels
("Aufgabe", "Wortlaut des Nutzers"), and for short missions the verbatim German
utterance is handed to the worker unchanged (the decomposer's heuristic
single-step path never rewrites it). With no instruction to the contrary, the
worker mirrors the request language and writes German identifiers, comments and
docstrings — a direct violation of the repo Output Language Policy ("every
artifact an agent produces is English").

The fix is one directive, prepended to the worker prompt at the one place every
worker prompt is built (``orchestrator._run_iterations``), so it covers all
decomposition paths (heuristic single-step, LLM multi-step), all worker CLIs
(Claude / Codex / Gemini), every critic iteration, and any future spawn entry
point — provider-agnostically.

The directive is deliberately a *default*, not an absolute: it governs the
*medium* (code), never the *subject matter*. A deliverable whose content is
meant to be German (a German news page, quoted text, localized copy) stays
German; only the surrounding code artifacts default to English.
"""
from __future__ import annotations

from typing import Final

# Prepended to every worker prompt. Kept provider-agnostic and language-neutral:
# it must hold no matter what language the request arrived in (the leak was
# "German request -> German code"), so it can never be a de/en toggle.
ARTIFACT_LANGUAGE_DIRECTIVE: Final[str] = (
    "ARTIFACT LANGUAGE - DEFAULT ENGLISH (this is an international repository). "
    "Write every CODE ARTIFACT in English: identifiers, function/class/variable "
    "names, comments, docstrings, log and error messages, commit messages, test "
    "names, and technical documentation. This holds even when this task "
    "description, the user's words, or the surrounding conversation are in "
    "another language - do NOT mirror the request language into the code. "
    "EXCEPTION: keep another language only where that language is the actual "
    "SUBJECT MATTER rather than the medium - user-facing copy / UI text or "
    "document content in the language the user asked for, quoted or localized "
    "text, and test fixtures that assert on non-English input. When the user "
    "EXPLICITLY requests a specific language for the artifact itself, honor that "
    "request."
)


# Prepended to every worker prompt, right after the language directive. Root
# cause (2026-06-22, mission 019ef052): the user asked for a SINGLE HTML file;
# the worker shipped four (index.html + app.js + styles.css + assets/). The
# standing _QUALITY_DIRECTIVE ("never downgrade to a minimal version, treat a
# skeleton hint as a floor not a ceiling") reads a single self-contained file as
# a forbidden minimal version, so the worker built the "production-quality" norm
# of a split multi-file project — overriding the user's explicit form constraint.
# Nothing in the chain told the worker that honoring a requested SHAPE is part of
# the job. This directive is that missing layer. Kept ASCII / provider- and
# language-agnostic: a form constraint must be honored no matter the request
# language or which worker CLI runs.
OUTPUT_SHAPE_DIRECTIVE: Final[str] = (
    "OUTPUT SHAPE - HONOR EXPLICIT FORM CONSTRAINTS. When the request fixes the "
    "SHAPE or packaging of the deliverable - a single self-contained file, one "
    "HTML file with its CSS and JS inlined, exactly one script, a specific file "
    "count, or a named output format - treat that as a HARD requirement and "
    "deliver in EXACTLY that form, even when it means inlining or consolidating "
    "what you would otherwise split across several files. Honoring a constraint "
    "the user set is part of satisfying the request; it is NEVER a downgrade, a "
    "stub, or a minimal version. When the request fixes no shape, use your best "
    "professional judgment for how to package the result."
)


def compose_worker_prompt(prior_block: str, step_prompt: str) -> str:
    """Build the full worker prompt: framing directives first.

    Leads with the artifact-language directive and then the output-shape
    directive, so both frame the whole task before any prior feedback or
    instruction is read. ``prior_block`` is the (possibly empty) rendered
    critic-reflection block that carries correction context across iterations.
    ``step_prompt`` is the task instruction for this step (verbatim user
    utterance or a decomposed sub-task).
    """
    head = f"{ARTIFACT_LANGUAGE_DIRECTIVE}\n\n{OUTPUT_SHAPE_DIRECTIVE}"
    if prior_block and prior_block.strip():
        return f"{head}\n\n{prior_block}\n\n{step_prompt}"
    return f"{head}\n\n{step_prompt}"


__all__ = [
    "ARTIFACT_LANGUAGE_DIRECTIVE",
    "OUTPUT_SHAPE_DIRECTIVE",
    "compose_worker_prompt",
]
