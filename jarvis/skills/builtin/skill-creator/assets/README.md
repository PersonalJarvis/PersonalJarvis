# assets/

Static files that style or fill the outputs produced by the skill:
templates (e.g. ``report.tex``), icons, configs, sample data.

**Jarvis adaptation note:** Anthropic's original ships ``eval_review.html``
(viewer HTML) here. Since we have not ported an eval framework, the folder is empty.

**Example Jarvis usage:**
- ``report_template.md`` — Markdown template with ``{{placeholder}}`` slots
- ``default_config.toml`` — template for skill-specific configs
- ``tts_voice_samples.json`` — voice presets for the TTS part of the skill

Files here are **read-only** at runtime — the runner copies them into the
output folder on demand and fills in the placeholders.
