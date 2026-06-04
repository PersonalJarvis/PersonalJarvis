# scripts/

Executable helper scripts for the skill. The runner can launch them via
``subprocess`` or Python import.

**Jarvis adaptation note:** Anthropic's original ships 8 OpenClaw-specific
Python files here (``run_eval.py``, ``aggregate_benchmark.py``, …). They are
not ported to Jarvis — they assume the OpenClaw subagent API, which we don't
have. This folder is intentionally empty; add your own helpers when you need
them.

**Example ideas for Jarvis scripts:**
- `validate_skill.py` — pydantic dry-run against a SKILL.md
- `package_skill.py` — zips the entire skill bundle for sharing
- `trigger_test.py` — fires a skill manually through the runner

Naming convention: snake_case.py. The entry function should be named `main()`
so the Jarvis runner can find it via reflection.
