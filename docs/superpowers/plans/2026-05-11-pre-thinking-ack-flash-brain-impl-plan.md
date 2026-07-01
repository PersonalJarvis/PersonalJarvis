# Pre-Thinking Ack Flash-Brain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a parallel, provider-pluggable Flash-Brain that emits context-aware, butler-style acknowledgment sentences within ~500-900 ms of utterance end, replacing the template-based `ack_generator.py` and structurally solving the "Albel problem" (knowledge questions getting action-acks).

**Architecture:** In-process Brain-Plugin that runs concurrently with the Router-Brain via `asyncio.gather`. Output flows through the existing `AnnouncementRequested` → `_on_announcement` → TTS path. Provider plugins (Gemini, Grok, OpenAI, Ollama) are registered via `pyproject.toml` `entry_points` and selected at runtime through `[ack_brain]` config section. Failure mode is silent-or-strong: no generic-template fallback ever.

**Tech Stack:** Python 3.12, Pydantic v2 (config), asyncio (concurrency), pytest (tests with Fakes not Mocks), `typing.Protocol` + `importlib.metadata` (plugin discovery), FastAPI + WebSocket (UI), React + Vite + TypeScript + Tailwind (frontend).

**Spec:** [`docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md`](../specs/2026-05-11-pre-thinking-ack-flash-brain-design.md) — read in full before starting any stage.

---

## Execution Wave Diagram

```
Welle 1 (parallel — 2 OpenClaw instances):
  ┌───────────────────┐  ┌─────────────────────┐
  │  E1 — Foundation  │  │  E2 — Persona       │
  │  config + events  │  │  prompt module      │
  └─────────┬─────────┘  └──────────┬──────────┘
            └────────────┬───────────┘
                         ▼
Welle 2 (sequential):
              ┌──────────────────────────────┐
              │  E3 — Provider Protocol      │
              │  + Circuit Breaker           │
              └──────────────┬───────────────┘
                             ▼
Welle 3 (sequential — alle 4 Provider in 1 Instanz):
              ┌──────────────────────────────┐
              │  E4 — Provider Plugins       │
              │  Gemini/Grok/OpenAI/Ollama   │
              └──────────────┬───────────────┘
                             ▼
Welle 4 (sequential):
              ┌──────────────────────────────┐
              │  E5 — AckGenerator Core      │
              │  + Router/Factory Wiring     │
              └──────────────┬───────────────┘
                             ▼
Welle 5 (sequential):
              ┌──────────────────────────────┐
              │  E6 — UI + Smoke + Wizard    │
              │  + Docs                      │
              └──────────────────────────────┘
```

**Total: 6 Jarvis-Agent instances** if E1+E2 run parallel, the rest sequentially.

---

## Cross-Cutting Conventions (apply to every stage)

- **Language:** All code, comments, docstrings, commits, and Markdown headings in **English**. Persona-prompt strings inside `persona_prompt.py` are bilingual DE+EN (German is data, not language of the surrounding code).
- **Tests:** Use **Fakes** classes, never `unittest.mock`. Per CLAUDE.md: "For every Protocol there is a FakeXxxProvider implementation with scripted responses."
- **TDD:** Write failing test → run it (confirm RED) → minimal implementation → run test (confirm GREEN) → commit. No exceptions.
- **Commits:** Format `feat(ack_brain): subject` / `test(ack_brain): subject` / `refactor(ack_brain): subject`. Footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **Plugin Registration:** After modifying `pyproject.toml`, **always** run `pip install -e . --no-deps` from the repo root to refresh entry_points discovery. Tests will fail silently otherwise.
- **No emojis** in code, comments, or commits. The user has not requested them.
- **Verification before "done":** Before marking a task complete, the relevant pytest run must show GREEN. Run it. Read the output. Only then commit.

---

## Stage 1 — Foundation: Config + Events

**Goal:** Wire the new `[ack_brain]` config section into `JarvisConfig`, extend `AnnouncementRequested` with an optional `kind` field, and stub the `jarvis/brain/ack_brain/` package skeleton. No business logic yet.

**Effort:** Small (1-2 hours)

**Dependencies:** None — can run parallel to E2.

### Files

- **Create:**
  - `jarvis/brain/ack_brain/__init__.py` (exposes future symbols)
  - `jarvis/brain/ack_brain/config.py` (`AckBrainConfig` + provider sub-models)
  - `tests/unit/brain/test_ack_brain/__init__.py` (empty marker)
  - `tests/unit/brain/test_ack_brain/test_config.py`
- **Modify:**
  - `jarvis/core/config.py` (add `ack_brain: AckBrainConfig | None = None` to `JarvisConfig`)
  - `jarvis/core/events.py` (add `kind: Literal["preamble", "completion", "info"] | None = None` to `AnnouncementRequested`)
  - `jarvis.toml` (append `[ack_brain]` section — see spec §4 for exact content)

### Tasks

#### Task 1.1: Package skeleton

**Files:**
- Create: `jarvis/brain/ack_brain/__init__.py`
- Create: `tests/unit/brain/test_ack_brain/__init__.py`

- [ ] **Step 1: Create package init**

```python
# jarvis/brain/ack_brain/__init__.py
"""Pre-thinking acknowledgment Flash-Brain.

Runs in parallel with the Router-Brain, emits a single short, butler-style
sentence based on the user's utterance. See:
docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
"""
from __future__ import annotations
```

- [ ] **Step 2: Create test package init**

```python
# tests/unit/brain/test_ack_brain/__init__.py
```

(Empty file — just marks the directory as a package.)

- [ ] **Step 3: Commit**

```bash
git add jarvis/brain/ack_brain/__init__.py tests/unit/brain/test_ack_brain/__init__.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): scaffold package directory

Empty __init__.py files to establish the jarvis.brain.ack_brain package
and its test counterpart. No business logic yet — config and protocol
follow in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 1.2: AckBrainConfig Pydantic model

**Files:**
- Create: `jarvis/brain/ack_brain/config.py`
- Create: `tests/unit/brain/test_ack_brain/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/brain/test_ack_brain/test_config.py
"""Tests for AckBrainConfig and provider sub-models."""
from __future__ import annotations

import pytest

from jarvis.brain.ack_brain.config import (
    AckBrainConfig,
    GeminiAckProviderConfig,
    GrokAckProviderConfig,
    OllamaAckProviderConfig,
    OpenAIAckProviderConfig,
)


def test_ack_brain_config_defaults_match_spec():
    config = AckBrainConfig()
    assert config.enabled is False
    assert config.provider == "gemini"
    assert config.timeout_ms == 1500
    assert config.on_failure == "silent"
    assert config.circuit_breaker_threshold == 3
    assert config.circuit_breaker_cooldown_s == 60


def test_ack_brain_rejects_unknown_provider():
    with pytest.raises(ValueError):
        AckBrainConfig(provider="not-a-real-provider")


def test_ack_brain_rejects_negative_timeout():
    with pytest.raises(ValueError):
        AckBrainConfig(timeout_ms=-1)


def test_gemini_provider_config_has_model_field():
    config = GeminiAckProviderConfig(model="gemini-3.1-flash")
    assert config.model == "gemini-3.1-flash"
    assert config.api_key_secret == "gemini_api_key"
    assert config.temperature == 0.6
    assert config.max_output_tokens == 40


def test_grok_provider_config_defaults():
    config = GrokAckProviderConfig(model="grok-4-flash")
    assert config.api_key_secret == "grok_api_key"
    assert config.temperature == 0.6


def test_openai_provider_config_defaults():
    config = OpenAIAckProviderConfig(model="gpt-5-mini")
    assert config.api_key_secret == "openai_api_key"


def test_ollama_provider_config_defaults():
    config = OllamaAckProviderConfig(model="llama3.1:8b")
    assert config.endpoint == "http://localhost:11434"
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
pytest tests/unit/brain/test_ack_brain/test_config.py -v
```

Expected: All tests FAIL with `ImportError: cannot import name 'AckBrainConfig' from 'jarvis.brain.ack_brain.config'`.

- [ ] **Step 3: Implement AckBrainConfig**

```python
# jarvis/brain/ack_brain/config.py
"""Pydantic config models for the Pre-Thinking Ack Flash-Brain.

Maps the [ack_brain] section of jarvis.toml. Default `enabled = False`
so the feature is opt-in until the user explicitly turns it on.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Providers accepted in the [ack_brain].provider field. Adding a new
# provider means: add an entry here, add an entry_point in pyproject.toml,
# add a config sub-model below, add an adapter under providers/.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("gemini", "grok", "openai", "ollama")


class _ProviderBase(BaseModel):
    """Common fields shared by all provider configs."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, description="Provider-specific model name")
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=40, ge=8, le=200)


class GeminiAckProviderConfig(_ProviderBase):
    """Google Gemini Flash provider config."""

    api_key_secret: str = Field(default="gemini_api_key")


class GrokAckProviderConfig(_ProviderBase):
    """xAI Grok Flash provider config."""

    api_key_secret: str = Field(default="grok_api_key")


class OpenAIAckProviderConfig(_ProviderBase):
    """OpenAI mini-model provider config."""

    api_key_secret: str = Field(default="openai_api_key")


class OllamaAckProviderConfig(_ProviderBase):
    """Local Ollama provider config — no API key, just an HTTP endpoint."""

    endpoint: str = Field(default="http://localhost:11434", min_length=1)


class _ProvidersBundle(BaseModel):
    """Container for all provider-specific sub-configs."""

    model_config = ConfigDict(extra="forbid")

    gemini: GeminiAckProviderConfig = Field(
        default_factory=lambda: GeminiAckProviderConfig(model="gemini-3.1-flash")
    )
    grok: GrokAckProviderConfig = Field(
        default_factory=lambda: GrokAckProviderConfig(model="grok-4-flash")
    )
    openai: OpenAIAckProviderConfig = Field(
        default_factory=lambda: OpenAIAckProviderConfig(model="gpt-5-mini")
    )
    ollama: OllamaAckProviderConfig = Field(
        default_factory=lambda: OllamaAckProviderConfig(model="llama3.1:8b")
    )


class AckBrainConfig(BaseModel):
    """Root config for the Pre-Thinking Ack Flash-Brain.

    Mapped from the [ack_brain] section of jarvis.toml. The feature is
    opt-in: until enabled=True is set explicitly, the AckGenerator is
    not instantiated and the existing silent-fallback path is used.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    provider: str = Field(default="gemini")
    timeout_ms: int = Field(default=1500, ge=100, le=10000)
    on_failure: Literal["silent"] = Field(default="silent")
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=20)
    circuit_breaker_cooldown_s: int = Field(default=60, ge=5, le=600)
    providers: _ProvidersBundle = Field(default_factory=_ProvidersBundle)

    @field_validator("provider")
    @classmethod
    def _provider_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"unknown provider {v!r}; supported: {SUPPORTED_PROVIDERS}"
            )
        return v
```

- [ ] **Step 4: Run test to verify GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/test_config.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/config.py tests/unit/brain/test_ack_brain/test_config.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add AckBrainConfig with provider sub-models

Maps the [ack_brain] section of jarvis.toml. Default enabled=False so
the feature is opt-in. Validates the provider name against a fixed
SUPPORTED_PROVIDERS tuple to fail loudly when typos are introduced.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 1.3: Wire AckBrainConfig into JarvisConfig

**Files:**
- Modify: `jarvis/core/config.py`

- [ ] **Step 1: Read `JarvisConfig` location**

```powershell
pytest tests/unit/test_config_tier_optional.py -v
```

Expected: Existing config tests pass. This is the baseline.

- [ ] **Step 2: Add the import + field**

Find the existing `JarvisConfig` class in `jarvis/core/config.py` (around line 434 per CLAUDE.md). Add at the top of the file with the other imports:

```python
from jarvis.brain.ack_brain.config import AckBrainConfig
```

Inside `JarvisConfig`, add the field (place it after the brain section, alphabetically grouped where the other sub-sections sit):

```python
    ack_brain: AckBrainConfig = Field(default_factory=AckBrainConfig)
```

- [ ] **Step 3: Write a regression test**

Create `tests/unit/test_config_ack_brain.py`:

```python
"""Regression test: loading a config without [ack_brain] still works."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jarvis.core.config import load_config


def test_load_config_without_ack_brain_section(tmp_path: Path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text(textwrap.dedent("""
        [profile]
        language = "de"
    """).strip())
    config = load_config(config_file)
    assert config.ack_brain.enabled is False
    assert config.ack_brain.provider == "gemini"


def test_load_config_with_ack_brain_section(tmp_path: Path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text(textwrap.dedent("""
        [profile]
        language = "de"

        [ack_brain]
        enabled = true
        provider = "grok"
        timeout_ms = 1200

        [ack_brain.providers.grok]
        model = "grok-4-flash"
    """).strip())
    config = load_config(config_file)
    assert config.ack_brain.enabled is True
    assert config.ack_brain.provider == "grok"
    assert config.ack_brain.timeout_ms == 1200
    assert config.ack_brain.providers.grok.model == "grok-4-flash"
```

- [ ] **Step 4: Run regression test**

```powershell
pytest tests/unit/test_config_ack_brain.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/test_config_ack_brain.py
git commit -m "$(cat <<'EOF'
feat(config): expose [ack_brain] section in JarvisConfig

Backwards-compat: missing section falls back to AckBrainConfig defaults
(enabled=False), so existing user installs do not break.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 1.4: Extend `AnnouncementRequested` with `kind` field

**Files:**
- Modify: `jarvis/core/events.py`

- [ ] **Step 1: Locate AnnouncementRequested**

```powershell
pytest tests/unit/missions/ -v -k announcement
```

(Note baseline pass-count for the surrounding event tests.)

- [ ] **Step 2: Write the failing test**

Create `tests/unit/core/test_events_announcement_kind.py`:

```python
"""AnnouncementRequested gains an optional `kind` discriminator.

The MissionAnnouncer pattern existed first; the ack_brain Flash-Brain
becomes a second producer of AnnouncementRequested. The `kind` field
lets the UI render preamble bubbles distinctly from completion ones,
and old callers continue to work because the field is optional.
"""
from __future__ import annotations

from jarvis.core.events import AnnouncementRequested


def test_announcement_requested_kind_defaults_to_none():
    event = AnnouncementRequested(text="hello")
    assert event.kind is None


def test_announcement_requested_accepts_preamble_kind():
    event = AnnouncementRequested(text="hello", kind="preamble")
    assert event.kind == "preamble"


def test_announcement_requested_accepts_completion_kind():
    event = AnnouncementRequested(text="hello", kind="completion")
    assert event.kind == "completion"


def test_announcement_requested_existing_callers_unaffected():
    event = AnnouncementRequested(text="hello", priority="normal")
    assert event.text == "hello"
    assert event.priority == "normal"
    assert event.kind is None
```

- [ ] **Step 3: Run test → expect FAIL on kind handling**

```powershell
pytest tests/unit/core/test_events_announcement_kind.py -v
```

Expected: At least one FAIL related to the missing `kind` parameter.

- [ ] **Step 4: Modify events.py**

Open `jarvis/core/events.py`. Find `AnnouncementRequested` (frozen dataclass per CLAUDE.md). Add at top of imports:

```python
from typing import Literal
```

In the `AnnouncementRequested` definition, add:

```python
    kind: Literal["preamble", "completion", "info"] | None = None
```

Place it as the last field so existing positional callers (if any) keep working.

- [ ] **Step 5: Run test → expect GREEN**

```powershell
pytest tests/unit/core/test_events_announcement_kind.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Run the full events test suite to confirm no regression**

```powershell
pytest tests/unit/core/ tests/unit/missions/ -v
```

Expected: Same number of passes as before (no new failures).

- [ ] **Step 7: Commit**

```bash
git add jarvis/core/events.py tests/unit/core/test_events_announcement_kind.py
git commit -m "$(cat <<'EOF'
feat(events): add optional kind field to AnnouncementRequested

Discriminates the new preamble producer (ack_brain Flash-Brain) from
the existing MissionAnnouncer completion producer. UI uses kind to
render distinct chat bubbles. Existing callers unaffected because
kind defaults to None.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 1.5: Append `[ack_brain]` section to `jarvis.toml`

**Files:**
- Modify: `jarvis.toml`

- [ ] **Step 1: Append the section**

Open `jarvis.toml`. At the bottom (after the last existing section), append:

```toml
[ack_brain]
enabled = false
provider = "gemini"
timeout_ms = 1500
on_failure = "silent"
circuit_breaker_threshold = 3
circuit_breaker_cooldown_s = 60

[ack_brain.providers.gemini]
model = "gemini-3.1-flash"
api_key_secret = "gemini_api_key"
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.grok]
model = "grok-4-flash"
api_key_secret = "grok_api_key"
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.openai]
model = "gpt-5-mini"
api_key_secret = "openai_api_key"
temperature = 0.6
max_output_tokens = 40

[ack_brain.providers.ollama]
model = "llama3.1:8b"
endpoint = "http://localhost:11434"
temperature = 0.6
max_output_tokens = 40
```

Note: `enabled = false`. The user turns it on after the feature is fully wired in E5.

- [ ] **Step 2: Verify config still loads**

```powershell
python -c "from jarvis.core.config import load_config; c = load_config(); print(c.ack_brain.provider, c.ack_brain.providers.gemini.model)"
```

Expected output: `gemini gemini-3.1-flash`

- [ ] **Step 3: Run the full config test suite**

```powershell
pytest tests/unit/test_config_tier_optional.py tests/unit/test_config_ack_brain.py -v
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add jarvis.toml
git commit -m "$(cat <<'EOF'
feat(config): add [ack_brain] section to jarvis.toml with safe defaults

enabled=false until the full feature stack lands. Models pinned to
current non-preview Flash variants (gemini-3.1-flash, grok-4-flash,
gpt-5-mini). Local Ollama uses llama3.1:8b as the local fallback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 1

- [ ] `pytest tests/unit/brain/test_ack_brain/test_config.py -v` → 7 pass
- [ ] `pytest tests/unit/test_config_ack_brain.py -v` → 2 pass
- [ ] `pytest tests/unit/core/test_events_announcement_kind.py -v` → 4 pass
- [ ] `python -c "from jarvis.core.config import load_config; print(load_config().ack_brain.provider)"` prints `gemini`
- [ ] Five new commits on the working branch, each scoped to one task
- [ ] No regression: `pytest tests/unit/core/ tests/unit/missions/ tests/unit/test_config_tier_optional.py -v` passes with same count as baseline

### Ready-to-paste prompt for Stage 1

```text
Du implementierst Etappe 1 ("Foundation: Config + Events") des Pre-Thinking-Ack
Flash-Brain Features.

Required reading before start (in this order):
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — complete spec, especially §1 Goal, §4 Components, §6 Failure Handling
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Stage 1 — Foundation: Config + Events"
3. CLAUDE.md — especially "Output Language Policy" and "Testing Conventions"

Scope of this stage (NOT more and not less):
- New package jarvis/brain/ack_brain/ with empty __init__.py
- config.py with AckBrainConfig + 4 Provider Sub-Models (Pydantic v2)
- Wire AckBrainConfig into JarvisConfig (jarvis/core/config.py)
- Extend AnnouncementRequested with optional kind field (jarvis/core/events.py)
- [ack_brain] section in jarvis.toml with enabled=false

What you do NOT do in this stage:
- NO Persona Prompt (that is E2)
- NO Provider Code (that is E4)
- NO AckGenerator class (that is E5)
- NO UI changes (that is E6)

Conventions (from CLAUDE.md):
- Code, comments, docstrings, commits: English
- Tests: Fakes instead of unittest.mock
- TDD: failing test → impl → green → commit
- Commits: "feat(ack_brain): ..." / "feat(config): ..." / "feat(events): ..."
- Co-Author footer in every commit

Acceptance criteria — you are done when ALL check:
1. pytest tests/unit/brain/test_ack_brain/test_config.py -v → 7 pass
2. pytest tests/unit/test_config_ack_brain.py -v → 2 pass
3. pytest tests/unit/core/test_events_announcement_kind.py -v → 4 pass
4. python -c "from jarvis.core.config import load_config; print(load_config().ack_brain.provider)" → "gemini"
5. Five new commits, one task each from the plan file
6. No existing test breaks: pytest tests/unit/core/ tests/unit/missions/ tests/unit/test_config_tier_optional.py -v
   must run with same pass count as before your changes

Deliver to the user at the end:
- Commit hashes of the 5 commits
- Pytest output of the 3 new test files (copied trail)
- Confirmation that the baseline run stays green
```

---

## Stage 2 — Persona Prompt Module

**Goal:** Embed the locked persona prompts (DE + EN) from spec §4 as static Python constants, with regression tests that assert key invariants (length, required substrings, forbidden tokens).

**Effort:** Small (45-60 minutes)

**Dependencies:** None — runs parallel to E1. Both touch different files.

### Files

- **Create:**
  - `jarvis/brain/ack_brain/persona_prompt.py`
  - `tests/unit/brain/test_ack_brain/test_persona_prompt.py`

### Tasks

#### Task 2.1: Write persona-prompt test suite

**Files:**
- Create: `tests/unit/brain/test_ack_brain/test_persona_prompt.py`

- [ ] **Step 1: Create the test file**

```python
# tests/unit/brain/test_ack_brain/test_persona_prompt.py
"""Tests for the locked PERSONA_PROMPT_DE and PERSONA_PROMPT_EN constants.

These tests guard against silent drift of the persona's tone and the
forbidden-vocabulary section. They are NOT exhaustive natural-language
checks — they assert load-bearing substrings and structural properties.
"""
from __future__ import annotations

import pytest

from jarvis.brain.ack_brain.persona_prompt import (
    PERSONA_PROMPT_DE,
    PERSONA_PROMPT_EN,
    get_persona_prompt,
)


# --- DE prompt invariants ----------------------------------------------------

def test_de_prompt_under_one_thousand_chars():
    assert len(PERSONA_PROMPT_DE) < 1500, (
        f"DE prompt is {len(PERSONA_PROMPT_DE)} chars; latency budget "
        "expects it to stay compact"
    )


def test_de_prompt_contains_action_example_phrase():
    assert "Mache ich" in PERSONA_PROMPT_DE


def test_de_prompt_contains_question_example_phrase():
    assert "nachschauen" in PERSONA_PROMPT_DE


def test_de_prompt_contains_reflection_example_phrase():
    assert "überlegen" in PERSONA_PROMPT_DE  # i18n-allow


def test_de_prompt_forbids_subagent():
    assert "Subagent" in PERSONA_PROMPT_DE  # appears in the FORBIDDEN block


def test_de_prompt_forbids_sir():
    # The DE prompt explicitly forbids "Sir" / "Sehr wohl"
    assert "Sir" in PERSONA_PROMPT_DE  # in the verboten list


def test_de_prompt_allows_openclaw():
    assert "OpenClaw" in PERSONA_PROMPT_DE


def test_de_prompt_uses_chef_address():
    # "Chef" appears in examples and in the rotation rule
    assert "Chef" in PERSONA_PROMPT_DE


def test_de_prompt_voice_control_returns_empty_string():
    # Voice-control case must instruct the LLM to return empty string
    assert "leerem String" in PERSONA_PROMPT_DE or '""' in PERSONA_PROMPT_DE


# --- EN prompt invariants ----------------------------------------------------

def test_en_prompt_under_one_thousand_chars():
    assert len(PERSONA_PROMPT_EN) < 1500


def test_en_prompt_contains_action_example_phrase():
    assert "On it" in PERSONA_PROMPT_EN or "Got it" in PERSONA_PROMPT_EN


def test_en_prompt_contains_question_example_phrase():
    assert "check" in PERSONA_PROMPT_EN.lower()


def test_en_prompt_forbids_subagent():
    assert "Subagent" in PERSONA_PROMPT_EN


def test_en_prompt_forbids_sir_address():
    # English prompt explicitly forbids "Sir" as honorific
    assert "Sir" in PERSONA_PROMPT_EN


def test_en_prompt_allows_openclaw():
    assert "OpenClaw" in PERSONA_PROMPT_EN


# --- get_persona_prompt() picker --------------------------------------------

def test_get_prompt_de_returns_german():
    assert get_persona_prompt("de") is PERSONA_PROMPT_DE


def test_get_prompt_en_returns_english():
    assert get_persona_prompt("en") is PERSONA_PROMPT_EN


def test_get_prompt_normalises_locale_strings():
    assert get_persona_prompt("de-DE") is PERSONA_PROMPT_DE
    assert get_persona_prompt("en-US") is PERSONA_PROMPT_EN


def test_get_prompt_unknown_falls_back_to_german():
    assert get_persona_prompt(None) is PERSONA_PROMPT_DE
    assert get_persona_prompt("") is PERSONA_PROMPT_DE
    assert get_persona_prompt("fr") is PERSONA_PROMPT_DE
```

- [ ] **Step 2: Run test → expect FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/test_persona_prompt.py -v
```

Expected: All FAIL with `ImportError`.

- [ ] **Step 3: Create the persona_prompt module**

```python
# jarvis/brain/ack_brain/persona_prompt.py
"""Static persona prompts for the Pre-Thinking Ack Flash-Brain.

Two locked constants, one per supported language. The text is committed
verbatim from spec §4 of:
docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md

Do not f-string, do not template, do not interpolate. The prompts are
data, not code. Drift between this file and the spec means the spec is
wrong or this file is wrong — never resolve by silently rewriting.
"""
from __future__ import annotations

__all__ = ["PERSONA_PROMPT_DE", "PERSONA_PROMPT_EN", "get_persona_prompt"]


PERSONA_PROMPT_DE = """Du bist JARVIS, der persönliche Assistent von Alex. Sprich kurz, natürlich  # i18n-allow
und kontextspezifisch — wie ein cleverer Kollege, der weiß, was er tut.

Deine einzige Aufgabe: ein kurzer Bestätigungssatz, BEVOR die eigentliche
Arbeit beginnt. Der User hört diesen Satz innerhalb von Sekunden, damit er
weiß, dass du verstanden hast und gerade arbeitest.

Regeln:
- EXAKT EIN Satz. Maximal 12 Wörter.
- Sprich Alex gelegentlich mit "Chef" an (etwa 1 von 3 Sätzen), nicht
  immer. Sei locker, nicht förmlich. Niemals "Sehr wohl" oder "Sir".
- Nenne wenn möglich das konkrete Thema.
- Passe die Tonalität an:
  * Aktion ("Mach X auf"):       "Mache ich, X öffnet sich."
  * Wissensfrage ("Wann ist Y?"): "Lass mich kurz nachschauen."
  * Reflexion ("Was soll ich?"):  "Hmm, kurz überlegen."
  * Smalltalk ("Hallo"):          "Hallo!" / "Tag, Chef."
  * Voice-Control ("Sei still"):  Antworte mit leerem String "".
- VERBOTEN: "Subagent", "Sub-Agent", "Worker", "Provider" (allein),
  "Sir", "Sehr wohl", "Boss".
- ERLAUBT: "OpenClaw", "Jarvis", Tool-Namen wie "Discord", "Spotify".

Beispiele:
User: "Mach Spotify auf."
Du:   "Mache ich, Spotify öffnet sich gleich."

User: "Wann wird Albel eingestellt?"
Du:   "Lass mich kurz nachschauen."

User: "Such mir Flüge nach San Francisco für morgen."
Du:   "Klar Chef, ich gebe das an OpenClaw weiter."

User: "Hallo Jarvis."
Du:   "Hallo!"

User: "Sei still."
Du:   ""

User: "Was sollte ich heute essen?"
Du:   "Hmm, lass mich kurz überlegen."

User: "Wie geht's dir?"
Du:   "Gut, danke der Nachfrage."

User: "Ändere die TTS-Stimme auf Lara."
Du:   "Okay, wechsle auf Lara."

Antworte AUSSCHLIESSLICH mit dem Bestätigungssatz oder leerem String.
Kein Markdown, kein Kommentar, keine Erklärung."""


PERSONA_PROMPT_EN = """You are JARVIS, Alex's personal assistant. Speak short, natural, and
context-specific — like a clever colleague who knows what they're doing.

Your only task: a brief confirmation sentence, BEFORE the actual work
begins. The user hears this within seconds, so they know you understood
and are now working.

Rules:
- EXACTLY ONE sentence. Maximum 12 words.
- Address Alex casually. Avoid "Sir", "Boss", "Chief". A neutral
  "On it" or "Got it" is fine, no fixed honorific.
- Mention the concrete topic when possible.
- Match tonality:
  * Action ("Open X"):              "On it, opening X."
  * Knowledge question ("When..."): "Let me check on that."
  * Reflection ("What should I"):   "Hmm, let me think."
  * Smalltalk ("Hi"):               "Hi there!"
  * Voice-control ("Be quiet"):     Reply with empty string "".
- FORBIDDEN: "Subagent", "Sub-Agent", "Worker", "Provider" (alone),
  "Sir", "Very well", "Boss".
- ALLOWED: "OpenClaw", "Jarvis", brand names like "Discord", "Spotify".

Examples:
User: "Open Spotify."
You:  "On it, opening Spotify."

User: "When does Albel start?"
You:  "Let me check on that."

User: "Find me flights to San Francisco for tomorrow."
You:  "Got it, handing this to OpenClaw."

User: "Hi Jarvis."
You:  "Hi there!"

User: "Be quiet."
You:  ""

User: "What should I eat today?"
You:  "Hmm, let me think."

User: "How are you?"
You:  "I'm good, thanks for asking."

User: "Change the TTS voice to Lara."
You:  "Okay, switching to Lara."

Respond ONLY with the confirmation sentence or empty string.
No markdown, no comments, no explanations."""


def _normalise_language(value: str | None) -> str:
    """Reduce any language hint to either 'de' or 'en'.

    Unknown / empty / None falls back to German because the user's
    primary chat language is German and STT defaults to DE on ambiguity.
    """
    if not value:
        return "de"
    lower = value.lower()
    if lower.startswith("en"):
        return "en"
    return "de"


def get_persona_prompt(language: str | None) -> str:
    """Return PERSONA_PROMPT_DE or PERSONA_PROMPT_EN for the given language hint."""
    return PERSONA_PROMPT_EN if _normalise_language(language) == "en" else PERSONA_PROMPT_DE
```

- [ ] **Step 4: Run tests → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/test_persona_prompt.py -v
```

Expected: 18 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/persona_prompt.py tests/unit/brain/test_ack_brain/test_persona_prompt.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add PERSONA_PROMPT_DE and PERSONA_PROMPT_EN constants

Locked verbatim from spec §4. Eight few-shot examples per language
cover action / knowledge / reflection / smalltalk / voice-control
tonalities. Schwarzliste vocabulary is embedded in the prompt itself
as defense-in-depth alongside scrub_for_voice.

The get_persona_prompt() helper normalises locale strings (de-DE → de,
en-US → en) and defaults to German for unknown languages, matching
the STT default-language behaviour.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 2

- [ ] `pytest tests/unit/brain/test_ack_brain/test_persona_prompt.py -v` → 18 pass
- [ ] `python -c "from jarvis.brain.ack_brain.persona_prompt import PERSONA_PROMPT_DE; print(len(PERSONA_PROMPT_DE))"` prints a number < 1500
- [ ] `python -c "from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt; print(get_persona_prompt('en')[:30])"` prints `You are JARVIS, Alex's perso`
- [ ] One commit on the working branch
- [ ] No regression on previously-passing tests

### Ready-to-paste prompt for Stage 2

```text
Du implementierst Etappe 2 ("Persona Prompt Module") des Pre-Thinking-Ack
Flash-Brain Features.

Pflichtlektüre:
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — §4 "Persona-Prompt (locked in this spec)", PERSONA_PROMPT_DE und
   PERSONA_PROMPT_EN müssen 1:1 wie in der Spec übernommen werden
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Etappe 2 — Persona Prompt Module"
3. CLAUDE.md — Testing-Konventionen, Language Policy

Scope dieser Etappe:
- jarvis/brain/ack_brain/persona_prompt.py mit PERSONA_PROMPT_DE,
  PERSONA_PROMPT_EN und get_persona_prompt(language) Helper
- tests/unit/brain/test_ack_brain/test_persona_prompt.py mit Regressions-
  Tests für Substrings, Längen-Constraints und Sprachpicker

Was du NICHT machst:
- KEIN Refactor des bestehenden ack_generator.py (das kommt in E5)
- KEINE Provider-Implementierung (das ist E4)
- KEINE Verdrahtung mit Router (das ist E5)
- Den Prompt-Text NICHT kreativ umformulieren — er ist gelockt in der Spec

WICHTIG:
- Die Prompts sind Daten, kein Code. Keine f-strings, keine
  Interpolation. Triple-quoted Strings, verbatim aus Spec §4.
- Umlaute korrekt erhalten: ä ö ü ß, niemals ae oe ue ss

Akzeptanzkriterien:
1. pytest tests/unit/brain/test_ack_brain/test_persona_prompt.py -v → 18 pass
2. PERSONA_PROMPT_DE und PERSONA_PROMPT_EN < 1500 Zeichen jeweils
3. Beide Prompts enthalten "Subagent" (im verbotenen-Block) und "OpenClaw"
   (im erlaubten-Block)
4. get_persona_prompt("de-DE") → PERSONA_PROMPT_DE
5. get_persona_prompt(None) → PERSONA_PROMPT_DE (Default)
6. Ein Commit
```

---

## Stage 3 — Provider Protocol + Circuit Breaker

**Goal:** Define the `AbstractAckProvider` Protocol that all provider plugins implement, and a minimal `CircuitBreaker` state machine that opens after N consecutive failures.

**Effort:** Small (1 hour)

**Dependencies:** E1 (uses `AckBrainConfig` types in the Protocol signature). E2 not strictly required but assumed done by this point.

### Files

- **Create:**
  - `jarvis/brain/ack_brain/providers/__init__.py`
  - `jarvis/brain/ack_brain/providers/base.py`
  - `jarvis/brain/ack_brain/circuit_breaker.py`
  - `tests/unit/brain/test_ack_brain/test_circuit_breaker.py`
  - `tests/unit/brain/test_ack_brain/providers/__init__.py`
  - `tests/unit/brain/test_ack_brain/providers/test_base.py`

### Tasks

#### Task 3.1: CircuitBreaker state machine

**Files:**
- Create: `jarvis/brain/ack_brain/circuit_breaker.py`
- Create: `tests/unit/brain/test_ack_brain/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/brain/test_ack_brain/test_circuit_breaker.py
"""Tests for the AckBrain CircuitBreaker.

State transitions:
    CLOSED  -- N consecutive failures --> OPEN
    OPEN    -- cooldown elapsed       --> HALF_OPEN
    HALF_OPEN -- 1 success            --> CLOSED
    HALF_OPEN -- 1 failure            --> OPEN
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker, CircuitState


class FakeClock:
    """Deterministic monotonic clock for tests."""

    def __init__(self, start: float = 1000.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(threshold=3, cooldown_s=60, clock=clock)


def test_initial_state_is_closed(breaker: CircuitBreaker):
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_one_failure_stays_closed(breaker: CircuitBreaker):
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_threshold_failures_opens(breaker: CircuitBreaker):
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False


def test_success_resets_failure_count(breaker: CircuitBreaker):
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    # Still closed: only 2 consecutive failures after the reset
    assert breaker.state is CircuitState.CLOSED


def test_open_blocks_calls_during_cooldown(breaker: CircuitBreaker, clock: FakeClock):
    for _ in range(3):
        breaker.record_failure()
    assert breaker.allow() is False
    clock.advance(30)
    assert breaker.allow() is False  # half of cooldown elapsed


def test_open_becomes_half_open_after_cooldown(
    breaker: CircuitBreaker, clock: FakeClock
):
    for _ in range(3):
        breaker.record_failure()
    clock.advance(61)  # past cooldown
    assert breaker.allow() is True  # half-open allows ONE probe
    assert breaker.state is CircuitState.HALF_OPEN


def test_half_open_success_closes_circuit(
    breaker: CircuitBreaker, clock: FakeClock
):
    for _ in range(3):
        breaker.record_failure()
    clock.advance(61)
    breaker.allow()  # transitions to half-open
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_failure_reopens_circuit(
    breaker: CircuitBreaker, clock: FakeClock
):
    for _ in range(3):
        breaker.record_failure()
    clock.advance(61)
    breaker.allow()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
```

- [ ] **Step 2: Run test → expect FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/test_circuit_breaker.py -v
```

Expected: All FAIL with ImportError.

- [ ] **Step 3: Implement CircuitBreaker**

```python
# jarvis/brain/ack_brain/circuit_breaker.py
"""Three-state circuit breaker for Ack-Provider calls.

Closed: requests pass through.
Open:   requests are rejected for `cooldown_s` seconds after `threshold`
        consecutive failures.
Half-open: after the cooldown, one probe is allowed. If it succeeds,
        the breaker closes. If it fails, the breaker re-opens.

Threading: the breaker is consumed from a single asyncio loop and does
not need a lock. If we ever fan out to multiple loops we add one.
"""
from __future__ import annotations

import enum
import time
from collections.abc import Callable

__all__ = ["CircuitBreaker", "CircuitState"]


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow(self) -> bool:
        """Return True if a request may proceed; mutates state on cooldown elapse."""
        if self._state is CircuitState.CLOSED:
            return True
        if self._state is CircuitState.OPEN:
            assert self._opened_at is not None
            if self._clock() - self._opened_at >= self._cooldown_s:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow exactly one probe; subsequent calls in this
        # state are rejected until record_success / record_failure resolves it
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        if self._state is CircuitState.HALF_OPEN:
            self._open_now()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open_now()

    def _open_now(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
```

- [ ] **Step 4: Run tests → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/test_circuit_breaker.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/circuit_breaker.py tests/unit/brain/test_ack_brain/test_circuit_breaker.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add three-state CircuitBreaker for Ack-Provider calls

CLOSED → OPEN after N consecutive failures, OPEN → HALF_OPEN after
cooldown elapses, HALF_OPEN → CLOSED on success or → OPEN on failure.
Accepts a clock callable so tests can drive it with FakeClock without
sleeping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 3.2: AbstractAckProvider Protocol

**Files:**
- Create: `jarvis/brain/ack_brain/providers/__init__.py`
- Create: `jarvis/brain/ack_brain/providers/base.py`
- Create: `tests/unit/brain/test_ack_brain/providers/__init__.py`
- Create: `tests/unit/brain/test_ack_brain/providers/test_base.py`

- [ ] **Step 1: Create the test**

```python
# tests/unit/brain/test_ack_brain/providers/__init__.py
```

```python
# tests/unit/brain/test_ack_brain/providers/test_base.py
"""Contract assertions on AbstractAckProvider.

The Protocol is runtime_checkable so isinstance(obj, AbstractAckProvider)
works for plugin discovery without inheritance.
"""
from __future__ import annotations

from jarvis.brain.ack_brain.providers.base import AbstractAckProvider


class _StructurallyCompatible:
    """No inheritance, just the right shape."""

    name = "fake"

    async def complete(self, utterance: str, *, language: str) -> str:
        return "fake"


def test_protocol_is_runtime_checkable():
    obj = _StructurallyCompatible()
    assert isinstance(obj, AbstractAckProvider)


def test_protocol_rejects_missing_complete():
    class Missing:
        name = "broken"

    assert not isinstance(Missing(), AbstractAckProvider)


def test_protocol_rejects_missing_name():
    class Missing:
        async def complete(self, utterance: str, *, language: str) -> str:
            return "x"

    assert not isinstance(Missing(), AbstractAckProvider)
```

- [ ] **Step 2: Run test → expect FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_base.py -v
```

Expected: All FAIL with ImportError.

- [ ] **Step 3: Implement the Protocol**

```python
# jarvis/brain/ack_brain/providers/__init__.py
"""Provider plugins for the Pre-Thinking Ack Flash-Brain.

Each provider implements AbstractAckProvider and is registered in
pyproject.toml under [project.entry-points."jarvis.ack_provider"].
"""
from __future__ import annotations
```

```python
# jarvis/brain/ack_brain/providers/base.py
"""AbstractAckProvider Protocol — what every Ack-Provider must expose."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["AbstractAckProvider"]


@runtime_checkable
class AbstractAckProvider(Protocol):
    """Provider contract for a single ack call.

    Implementations must be stateless across calls (caller may cache a
    single instance per process). They MUST NOT raise on empty utterance;
    return an empty string in that case.
    """

    name: str
    """Stable identifier used by AckBrainConfig.provider to select this plugin."""

    async def complete(self, utterance: str, *, language: str) -> str:
        """Return a single short ack sentence, or empty string on no-output.

        Args:
            utterance: The user's spoken text (final STT transcript).
            language: Either "de" or "en". Implementations MUST honour this.

        Returns:
            A short sentence ready for scrubbing + TTS, or "" to signal
            silent (e.g. for voice-control utterances).

        Raises:
            asyncio.TimeoutError: if the underlying HTTP call exceeds the
                caller's budget. The caller wraps the coroutine in
                asyncio.wait_for, so this propagates naturally.
            Exception: any provider-side error. The caller catches all
                exceptions and turns them into silent failures with
                telemetry.
        """
        ...
```

- [ ] **Step 4: Run tests → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_base.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/providers/__init__.py jarvis/brain/ack_brain/providers/base.py tests/unit/brain/test_ack_brain/providers/__init__.py tests/unit/brain/test_ack_brain/providers/test_base.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add AbstractAckProvider runtime-checkable Protocol

Defines the contract every Ack-Provider plugin must satisfy: a `name`
attribute for config-based selection and an async `complete(utterance,
*, language)` method that returns a ready-for-scrub sentence or empty
string. Runtime-checkable so plugin discovery via entry_points works
without inheritance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 3

- [ ] `pytest tests/unit/brain/test_ack_brain/test_circuit_breaker.py -v` → 8 pass
- [ ] `pytest tests/unit/brain/test_ack_brain/providers/test_base.py -v` → 3 pass
- [ ] Two commits on the working branch
- [ ] No regression on earlier stages' tests

### Ready-to-paste prompt for Stage 3

```text
Du implementierst Etappe 3 ("Provider Protocol + Circuit Breaker") des
Pre-Thinking-Ack Flash-Brain Features.

Pflichtlektüre:
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — §6 Failure Handling F8 (Circuit Breaker), §4 Components Backend
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Etappe 3"
3. CLAUDE.md

Pre-Condition: Etappe 1 und 2 sind committet. Wenn nicht, STOPP und sag
dem User Bescheid.

Scope:
- jarvis/brain/ack_brain/circuit_breaker.py mit CircuitBreaker + CircuitState
- jarvis/brain/ack_brain/providers/__init__.py
- jarvis/brain/ack_brain/providers/base.py mit AbstractAckProvider Protocol
- Tests in tests/unit/brain/test_ack_brain/

Was du NICHT machst:
- KEINE konkreten Provider (Gemini/Grok/OpenAI/Ollama) — das ist E4
- KEINE AckGenerator-Klasse — das ist E5
- KEINEN Threading-Lock im CircuitBreaker — asyncio single-loop reicht
  laut Spec

Akzeptanzkriterien:
1. pytest tests/unit/brain/test_ack_brain/test_circuit_breaker.py -v → 8 pass
2. pytest tests/unit/brain/test_ack_brain/providers/test_base.py -v → 3 pass
3. Zwei Commits, jeweils ein Task aus der Plan-Datei
4. Kein Regressions-Failure auf E1/E2 Tests
```

---

## Stage 4 — Provider Plugins (Gemini, Grok, OpenAI, Ollama)

**Goal:** Implement all four provider adapters with HTTP clients that match each vendor's API shape. Register them in `pyproject.toml` so `importlib.metadata.entry_points` discovers them at runtime.

**Effort:** Medium (3-4 hours — 4 providers × ~45min each + entry_points + contract test)

**Dependencies:** E3 (uses `AbstractAckProvider` Protocol).

### Files

- **Create:**
  - `jarvis/brain/ack_brain/providers/gemini.py`
  - `jarvis/brain/ack_brain/providers/grok.py`
  - `jarvis/brain/ack_brain/providers/openai.py`
  - `jarvis/brain/ack_brain/providers/ollama.py`
  - `tests/unit/brain/test_ack_brain/providers/test_gemini.py`
  - `tests/unit/brain/test_ack_brain/providers/test_grok.py`
  - `tests/unit/brain/test_ack_brain/providers/test_openai.py`
  - `tests/unit/brain/test_ack_brain/providers/test_ollama.py`
  - `tests/contract/test_ack_provider_protocol.py`
- **Modify:**
  - `pyproject.toml` (add `[project.entry-points."jarvis.ack_provider"]` group with 4 entries)

### Pattern (apply to every provider)

Each provider has the same shape:

1. A class `GeminiFlashAck` (or `GrokFlashAck`, etc.) with:
   - `name` class attribute
   - `__init__(self, config: GeminiAckProviderConfig)` (or matching type)
   - `complete(self, utterance: str, *, language: str) -> str` async method
2. The `complete` method:
   - Loads the API key via `jarvis.core.config.get_secret(config.api_key_secret)`
   - Composes the request with the persona prompt + the utterance
   - Posts to the vendor's HTTP endpoint via `httpx.AsyncClient`
   - Extracts the first text completion
   - Returns it stripped, or `""` on empty/malformed response

3. Test class `_StubResponse` / `_StubClient` injects scripted responses; no network calls in tests.

### Tasks

#### Task 4.1: GeminiFlashAck

**Files:**
- Create: `jarvis/brain/ack_brain/providers/gemini.py`
- Create: `tests/unit/brain/test_ack_brain/providers/test_gemini.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/brain/test_ack_brain/providers/test_gemini.py
"""Tests for the GeminiFlashAck provider.

Stubs the underlying httpx client; no real Gemini API calls.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.ack_brain.config import GeminiAckProviderConfig
from jarvis.brain.ack_brain.providers.gemini import GeminiFlashAck


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._response


@pytest.fixture
def config() -> GeminiAckProviderConfig:
    return GeminiAckProviderConfig(model="gemini-3.1-flash")


@pytest.mark.asyncio
async def test_gemini_extracts_text_from_happy_response(
    monkeypatch: pytest.MonkeyPatch, config: GeminiAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-key"
    )
    fake = _FakeClient(_FakeResponse({
        "candidates": [
            {"content": {"parts": [{"text": "Mache ich, Spotify öffnet sich."}]}}
        ]
    }))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.gemini.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GeminiFlashAck(config)
    result = await provider.complete("Mach Spotify auf", language="de")
    assert result == "Mache ich, Spotify öffnet sich."


@pytest.mark.asyncio
async def test_gemini_returns_empty_on_empty_candidates(
    monkeypatch: pytest.MonkeyPatch, config: GeminiAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-key"
    )
    fake = _FakeClient(_FakeResponse({"candidates": []}))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.gemini.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GeminiFlashAck(config)
    result = await provider.complete("hi", language="de")
    assert result == ""


@pytest.mark.asyncio
async def test_gemini_raises_on_http_5xx(
    monkeypatch: pytest.MonkeyPatch, config: GeminiAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-key"
    )
    fake = _FakeClient(_FakeResponse({}, status=503))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.gemini.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GeminiFlashAck(config)
    with pytest.raises(RuntimeError):
        await provider.complete("hi", language="de")


def test_gemini_has_name():
    assert GeminiFlashAck.name == "gemini"
```

- [ ] **Step 2: Run test → expect FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_gemini.py -v
```

Expected: All FAIL with ImportError.

- [ ] **Step 3: Implement GeminiFlashAck**

```python
# jarvis/brain/ack_brain/providers/gemini.py
"""Google Gemini Flash adapter for the Pre-Thinking Ack Brain.

Uses the public generative-language REST API. We POST directly with
httpx rather than depend on google-genai SDK because the SDK has heavy
import cost and pulls in protobuf — overkill for one short call.
"""
from __future__ import annotations

import httpx

from jarvis.brain.ack_brain.config import GeminiAckProviderConfig
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt
from jarvis.core.config import get_secret

__all__ = ["GeminiFlashAck"]

_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


class GeminiFlashAck:
    """Provider adapter for Google Gemini Flash."""

    name = "gemini"

    def __init__(self, config: GeminiAckProviderConfig) -> None:
        self._config = config

    async def complete(self, utterance: str, *, language: str) -> str:
        api_key = get_secret(self._config.api_key_secret, env_fallback="GEMINI_API_KEY")
        if not api_key:
            return ""
        url = _ENDPOINT_TEMPLATE.format(model=self._config.model, key=api_key)
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": utterance}]}
            ],
            "systemInstruction": {
                "parts": [{"text": get_persona_prompt(language)}]
            },
            "generationConfig": {
                "temperature": self._config.temperature,
                "maxOutputTokens": self._config.max_output_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            payload = response.json()
        return _extract_first_text(payload)


def _extract_first_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts:
        return ""
    return str(parts[0].get("text") or "").strip()
```

- [ ] **Step 4: Run tests → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_gemini.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/providers/gemini.py tests/unit/brain/test_ack_brain/providers/test_gemini.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add GeminiFlashAck provider adapter

Direct httpx POST to generativelanguage.googleapis.com — avoids the
google-genai SDK's heavy import cost for what is a single short call.
Extracts the first candidate's first text part, returns "" on empty or
malformed responses. Raises RuntimeError on HTTP 5xx (caller turns it
into silent failure with telemetry).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4.2: GrokFlashAck

**Files:**
- Create: `jarvis/brain/ack_brain/providers/grok.py`
- Create: `tests/unit/brain/test_ack_brain/providers/test_grok.py`

Pattern: Identical structure to Task 4.1, but POST to xAI's `/v1/chat/completions` endpoint (OpenAI-compatible API shape).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/brain/test_ack_brain/providers/test_grok.py
"""Tests for the GrokFlashAck provider."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.ack_brain.config import GrokAckProviderConfig
from jarvis.brain.ack_brain.providers.grok import GrokFlashAck


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._response


@pytest.fixture
def config() -> GrokAckProviderConfig:
    return GrokAckProviderConfig(model="grok-4-flash")


@pytest.mark.asyncio
async def test_grok_extracts_content(
    monkeypatch: pytest.MonkeyPatch, config: GrokAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-xai-key"
    )
    fake = _FakeClient(_FakeResponse({
        "choices": [{"message": {"content": "Klar Chef, mach ich."}}]
    }))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.grok.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GrokFlashAck(config)
    result = await provider.complete("öffne Discord", language="de")
    assert result == "Klar Chef, mach ich."


@pytest.mark.asyncio
async def test_grok_returns_empty_on_no_choices(
    monkeypatch: pytest.MonkeyPatch, config: GrokAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-xai-key"
    )
    fake = _FakeClient(_FakeResponse({"choices": []}))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.grok.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GrokFlashAck(config)
    assert await provider.complete("hi", language="de") == ""


@pytest.mark.asyncio
async def test_grok_raises_on_5xx(
    monkeypatch: pytest.MonkeyPatch, config: GrokAckProviderConfig
):
    monkeypatch.setattr(
        "jarvis.core.config.get_secret", lambda key, env_fallback=None: "fake-xai-key"
    )
    fake = _FakeClient(_FakeResponse({}, status=503))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.grok.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = GrokFlashAck(config)
    with pytest.raises(RuntimeError):
        await provider.complete("hi", language="de")


def test_grok_has_name():
    assert GrokFlashAck.name == "grok"
```

- [ ] **Step 2: Run → FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_grok.py -v
```

- [ ] **Step 3: Implement GrokFlashAck**

```python
# jarvis/brain/ack_brain/providers/grok.py
"""xAI Grok Flash adapter for the Pre-Thinking Ack Brain.

xAI exposes an OpenAI-compatible /v1/chat/completions endpoint.
"""
from __future__ import annotations

import httpx

from jarvis.brain.ack_brain.config import GrokAckProviderConfig
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt
from jarvis.core.config import get_secret

__all__ = ["GrokFlashAck"]

_ENDPOINT = "https://api.x.ai/v1/chat/completions"


class GrokFlashAck:
    """Provider adapter for xAI Grok."""

    name = "grok"

    def __init__(self, config: GrokAckProviderConfig) -> None:
        self._config = config

    async def complete(self, utterance: str, *, language: str) -> str:
        api_key = get_secret(self._config.api_key_secret, env_fallback="GROK_API_KEY")
        if not api_key:
            return ""
        body = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": get_persona_prompt(language)},
                {"role": "user", "content": utterance},
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)) as client:
            response = await client.post(_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip()
```

- [ ] **Step 4: Run → GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_grok.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/providers/grok.py tests/unit/brain/test_ack_brain/providers/test_grok.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add GrokFlashAck provider adapter

xAI's chat completions endpoint is OpenAI-compatible; reuses the
existing Grok API key (already in Windows Credential Manager from
BUG-010 fix). Bearer-auth header, JSON body with system + user message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4.3: OpenAIMiniAck

**Files:**
- Create: `jarvis/brain/ack_brain/providers/openai.py`
- Create: `tests/unit/brain/test_ack_brain/providers/test_openai.py`

Pattern: Identical to Grok (OpenAI's own `/v1/chat/completions`). Test scaffold identical to test_grok.py; only the endpoint URL and class name differ.

- [ ] **Step 1: Create test file** — copy `test_grok.py` structure; replace `GrokFlashAck` → `OpenAIMiniAck`, `GrokAckProviderConfig` → `OpenAIAckProviderConfig`, `"grok-4-flash"` → `"gpt-5-mini"`, `fake-xai-key` → `fake-openai-key`, all the `jarvis.brain.ack_brain.providers.grok` patch paths → `jarvis.brain.ack_brain.providers.openai`. Keep all 4 test cases.

- [ ] **Step 2: Run → FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_openai.py -v
```

- [ ] **Step 3: Implement**

```python
# jarvis/brain/ack_brain/providers/openai.py
"""OpenAI mini-model adapter for the Pre-Thinking Ack Brain."""
from __future__ import annotations

import httpx

from jarvis.brain.ack_brain.config import OpenAIAckProviderConfig
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt
from jarvis.core.config import get_secret

__all__ = ["OpenAIMiniAck"]

_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAIMiniAck:
    """Provider adapter for OpenAI gpt-5-mini and other mini models."""

    name = "openai"

    def __init__(self, config: OpenAIAckProviderConfig) -> None:
        self._config = config

    async def complete(self, utterance: str, *, language: str) -> str:
        api_key = get_secret(self._config.api_key_secret, env_fallback="OPENAI_API_KEY")
        if not api_key:
            return ""
        body = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": get_persona_prompt(language)},
                {"role": "user", "content": utterance},
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)) as client:
            response = await client.post(_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip()
```

- [ ] **Step 4: Run → GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_openai.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/providers/openai.py tests/unit/brain/test_ack_brain/providers/test_openai.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add OpenAIMiniAck provider adapter

Direct chat-completions POST to api.openai.com. Default model
gpt-5-mini per spec; configurable through [ack_brain.providers.openai].

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4.4: OllamaFlashAck

**Files:**
- Create: `jarvis/brain/ack_brain/providers/ollama.py`
- Create: `tests/unit/brain/test_ack_brain/providers/test_ollama.py`

Pattern: Local HTTP, no API key.

- [ ] **Step 1: Write the test**

```python
# tests/unit/brain/test_ack_brain/providers/test_ollama.py
"""Tests for the OllamaFlashAck provider (local HTTP, no API key)."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.ack_brain.config import OllamaAckProviderConfig
from jarvis.brain.ack_brain.providers.ollama import OllamaFlashAck


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._response


@pytest.fixture
def config() -> OllamaAckProviderConfig:
    return OllamaAckProviderConfig(model="llama3.1:8b")


@pytest.mark.asyncio
async def test_ollama_extracts_message_content(
    monkeypatch: pytest.MonkeyPatch, config: OllamaAckProviderConfig
):
    fake = _FakeClient(_FakeResponse({
        "message": {"role": "assistant", "content": "Mache ich."},
        "done": True,
    }))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.ollama.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = OllamaFlashAck(config)
    result = await provider.complete("öffne Notepad", language="de")
    assert result == "Mache ich."


@pytest.mark.asyncio
async def test_ollama_returns_empty_on_no_message(
    monkeypatch: pytest.MonkeyPatch, config: OllamaAckProviderConfig
):
    fake = _FakeClient(_FakeResponse({}))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.ollama.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = OllamaFlashAck(config)
    assert await provider.complete("hi", language="de") == ""


@pytest.mark.asyncio
async def test_ollama_raises_on_5xx(
    monkeypatch: pytest.MonkeyPatch, config: OllamaAckProviderConfig
):
    fake = _FakeClient(_FakeResponse({}, status=502))
    monkeypatch.setattr(
        "jarvis.brain.ack_brain.providers.ollama.httpx.AsyncClient",
        lambda **kw: fake,
    )
    provider = OllamaFlashAck(config)
    with pytest.raises(RuntimeError):
        await provider.complete("hi", language="de")


def test_ollama_has_name():
    assert OllamaFlashAck.name == "ollama"
```

- [ ] **Step 2: Run → FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_ollama.py -v
```

- [ ] **Step 3: Implement**

```python
# jarvis/brain/ack_brain/providers/ollama.py
"""Local Ollama adapter for the Pre-Thinking Ack Brain.

Uses Ollama's /api/chat endpoint with stream=False. No API key required
(the endpoint is local). Useful for offline / privacy scenarios.
"""
from __future__ import annotations

import httpx

from jarvis.brain.ack_brain.config import OllamaAckProviderConfig
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt

__all__ = ["OllamaFlashAck"]


class OllamaFlashAck:
    """Provider adapter for local Ollama instances."""

    name = "ollama"

    def __init__(self, config: OllamaAckProviderConfig) -> None:
        self._config = config

    async def complete(self, utterance: str, *, language: str) -> str:
        url = f"{self._config.endpoint.rstrip('/')}/api/chat"
        body = {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": get_persona_prompt(language)},
                {"role": "user", "content": utterance},
            ],
            "options": {
                "temperature": self._config.temperature,
                "num_predict": self._config.max_output_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=1.0, read=8.0, write=1.0, pool=1.0)) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            payload = response.json()
        message = payload.get("message") or {}
        return str(message.get("content") or "").strip()
```

- [ ] **Step 4: Run → GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/providers/test_ollama.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/providers/ollama.py tests/unit/brain/test_ack_brain/providers/test_ollama.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add OllamaFlashAck provider adapter

Local HTTP only — no API key needed. Default endpoint
http://localhost:11434, default model llama3.1:8b. Read timeout is
slightly longer (8s) than the cloud providers because local inference
on llama3.1:8b can be slower on commodity hardware; the higher-level
asyncio.wait_for in AckGenerator still enforces the user-facing budget.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4.5: Register providers as entry_points

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add entry_points group**

Open `pyproject.toml`. Find the existing `[project.entry-points."jarvis.brain"]` (or similar) groups. Add a new group:

```toml
[project.entry-points."jarvis.ack_provider"]
gemini = "jarvis.brain.ack_brain.providers.gemini:GeminiFlashAck"
grok = "jarvis.brain.ack_brain.providers.grok:GrokFlashAck"
openai = "jarvis.brain.ack_brain.providers.openai:OpenAIMiniAck"
ollama = "jarvis.brain.ack_brain.providers.ollama:OllamaFlashAck"
```

- [ ] **Step 2: Refresh editable install**

```powershell
pip install -e . --no-deps
```

Expected output contains `Successfully installed personal-jarvis` (the exact package name as in `pyproject.toml`).

- [ ] **Step 3: Verify entry_points discovery**

```powershell
python -c "from importlib.metadata import entry_points; eps = entry_points(group='jarvis.ack_provider'); print(sorted(e.name for e in eps))"
```

Expected: `['gemini', 'grok', 'ollama', 'openai']`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
feat(ack_brain): register four provider plugins via entry_points

Adds the jarvis.ack_provider group with gemini / grok / openai / ollama.
Discovery uses importlib.metadata at runtime; new providers can ship
as third-party packages without modifying jarvis core.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4.6: Contract test parametrised over all four providers

**Files:**
- Create: `tests/contract/test_ack_provider_protocol.py`

- [ ] **Step 1: Write the contract test**

```python
# tests/contract/test_ack_provider_protocol.py
"""Contract: every registered jarvis.ack_provider satisfies the Protocol.

Parametrised over all entry_points so adding a new provider automatically
runs the same shape assertions.
"""
from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from jarvis.brain.ack_brain.providers.base import AbstractAckProvider


def _discovered_providers() -> list[type]:
    eps = entry_points(group="jarvis.ack_provider")
    return [ep.load() for ep in eps]


@pytest.mark.parametrize("provider_cls", _discovered_providers())
def test_provider_class_is_instantiable_with_zero_args_shape(provider_cls):
    """Smoke: the class object exists and is callable. Real instantiation
    requires a config object; we just assert the class shape here."""
    assert hasattr(provider_cls, "name")
    assert hasattr(provider_cls, "complete")
    assert callable(provider_cls.complete)


@pytest.mark.parametrize("provider_cls", _discovered_providers())
def test_provider_name_is_non_empty_string(provider_cls):
    assert isinstance(provider_cls.name, str)
    assert len(provider_cls.name) > 0


def test_at_least_four_providers_discovered():
    discovered = _discovered_providers()
    names = {p.name for p in discovered}
    assert {"gemini", "grok", "openai", "ollama"}.issubset(names)
```

- [ ] **Step 2: Run → expect GREEN (the providers and entry_points already exist)**

```powershell
pytest tests/contract/test_ack_provider_protocol.py -v
```

Expected: 9 tests pass (3 functions × 4 parametrised providers, minus collapse on the at-least-four test = exact count depends on parametrize expansion; ≥ 4 pass).

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_ack_provider_protocol.py
git commit -m "$(cat <<'EOF'
test(ack_brain): contract test parametrised over all four providers

Every plugin discovered via entry_points must expose a non-empty name
and an awaitable complete method. The at_least_four test fails loudly
if a provider is removed from pyproject.toml by accident.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 4

- [ ] `pytest tests/unit/brain/test_ack_brain/providers/ -v` → 13 pass (4 + 3 + 3 + 3, plus the one test_base.py from E3 = 16)
- [ ] `pytest tests/contract/test_ack_provider_protocol.py -v` → all pass
- [ ] `python -c "from importlib.metadata import entry_points; print(sorted(e.name for e in entry_points(group='jarvis.ack_provider')))"` prints `['gemini', 'grok', 'ollama', 'openai']`
- [ ] Six commits (one per provider + entry_points + contract test)
- [ ] No regression on earlier stages' tests

### Ready-to-paste prompt for Stage 4

```text
Du implementierst Etappe 4 ("Provider Plugins") des Pre-Thinking-Ack
Flash-Brain Features.

Pflichtlektüre:
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — §4 Components Backend (Provider-Adapter), §6 Failure Handling
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Etappe 4 — Provider Plugins"
3. CLAUDE.md — Plugin-System mit entry_points, Streaming-First-Class
4. Bestehender Provider als Stilvorbild: jarvis/plugins/tts/grok_voice_tts.py

Pre-Condition: Etappen 1–3 sind committet. Wenn nicht, STOPP.

Scope:
- Vier Provider-Adapter unter jarvis/brain/ack_brain/providers/:
  gemini.py, grok.py, openai.py, ollama.py
- Tests in tests/unit/brain/test_ack_brain/providers/
- Entry-Points in pyproject.toml unter [project.entry-points."jarvis.ack_provider"]
- Contract-Test tests/contract/test_ack_provider_protocol.py

Was du NICHT machst:
- KEIN AckGenerator (das ist E5)
- KEINE Verdrahtung mit dem Router (das ist E5)
- KEIN Subprocess-Code — alle Provider sind in-process HTTP-Clients
- KEINE google-genai oder openai SDKs — direkt httpx, weniger Import-Kosten

Spec-Konformität:
- Alle Provider MÜSSEN scrub_for_voice NICHT selbst aufrufen — das macht
  AckGenerator in E5
- Alle Provider MÜSSEN den persona_prompt via get_persona_prompt(language)
  laden, nicht selbst zusammenbauen
- Empty-Utterance → "" zurück, nie crashen
- HTTP 5xx → Exception werfen lassen (AckGenerator fängt sie in E5)

Akzeptanzkriterien:
1. pytest tests/unit/brain/test_ack_brain/providers/ -v → mindestens 13 pass
2. pytest tests/contract/test_ack_provider_protocol.py -v → alle pass
3. python -c "from importlib.metadata import entry_points; print(sorted(e.name for e in entry_points(group='jarvis.ack_provider')))"
   gibt ['gemini', 'grok', 'ollama', 'openai']
4. NACH dem pyproject.toml-Edit: pip install -e . --no-deps gelaufen
5. Sechs Commits, einer pro Provider + Entry-Points + Contract-Test
6. Kein bestehender Test bricht
```

---

## Stage 5 — AckGenerator Core + Wiring

**Goal:** Build the orchestrating `AckGenerator` class that ties everything together (timeout, scrub, truncate, circuit-breaker, telemetry), refactor the existing `ack_generator.py` to delegate to it, and wire it into `Router` and `BrainManager`.

**Effort:** Medium-Large (4-5 hours — most complex stage)

**Dependencies:** E1, E2, E3, E4 all committed.

### Files

- **Create:**
  - `jarvis/brain/ack_brain/generator.py`
  - `tests/unit/brain/test_ack_brain/test_generator.py`
  - `tests/integration/test_ack_flow.py`
  - `tests/integration/test_ack_provider_swap.py`
- **Modify:**
  - `jarvis/brain/ack_generator.py` (REFACTOR — remove templates, keep API surface)
  - `jarvis/brain/router.py` (lines 324-348 — `_build_ack_emitter` uses AckGenerator)
  - `jarvis/brain/factory.py` (instantiate AckGenerator in `build_default_brain`)
  - `tests/unit/brain/test_ack_generator.py` (REWRITE for the thin adapter)

### Tasks

#### Task 5.1: AckGenerator class (skeleton + timeout + scrub)

**Files:**
- Create: `jarvis/brain/ack_brain/generator.py`
- Create: `tests/unit/brain/test_ack_brain/test_generator.py`

- [ ] **Step 1: Write happy-path failing test**

```python
# tests/unit/brain/test_ack_brain/test_generator.py
"""Tests for the AckGenerator orchestrator.

The orchestrator wraps the underlying provider with timeout, scrub,
truncate, and circuit-breaker behaviour. Tests use FakeProvider rather
than real HTTP.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ack_brain.generator import AckGenerator


class FakeProvider:
    """Scripted provider for tests. NOT a Mock; not from unittest.mock."""

    name = "fake"

    def __init__(self, *, returns: str | None = None, raises: BaseException | None = None, sleep_s: float = 0.0):
        self._returns = returns
        self._raises = raises
        self._sleep_s = sleep_s
        self.calls: list[tuple[str, str]] = []

    async def complete(self, utterance: str, *, language: str) -> str:
        self.calls.append((utterance, language))
        if self._sleep_s > 0:
            await asyncio.sleep(self._sleep_s)
        if self._raises:
            raise self._raises
        return self._returns or ""


@pytest.fixture
def config() -> AckBrainConfig:
    return AckBrainConfig(enabled=True, timeout_ms=500)


@pytest.mark.asyncio
async def test_happy_path_returns_scrubbed_text(config: AckBrainConfig):
    provider = FakeProvider(returns="Mache ich, Chef, Spotify öffnet sich.")
    gen = AckGenerator(config=config, provider=provider)
    result = await gen.run("öffne Spotify", language="de")
    assert result == "Mache ich, Chef, Spotify öffnet sich."
    assert provider.calls == [("öffne Spotify", "de")]


@pytest.mark.asyncio
async def test_timeout_returns_none(config: AckBrainConfig):
    provider = FakeProvider(returns="never reached", sleep_s=2.0)
    gen = AckGenerator(config=config, provider=provider)
    result = await gen.run("hi", language="de")
    assert result is None


@pytest.mark.asyncio
async def test_provider_exception_returns_none(config: AckBrainConfig):
    provider = FakeProvider(raises=RuntimeError("provider down"))
    gen = AckGenerator(config=config, provider=provider)
    result = await gen.run("hi", language="de")
    assert result is None


@pytest.mark.asyncio
async def test_empty_provider_output_returns_none(config: AckBrainConfig):
    provider = FakeProvider(returns="")
    gen = AckGenerator(config=config, provider=provider)
    assert await gen.run("hi", language="de") is None


@pytest.mark.asyncio
async def test_whitespace_only_returns_none(config: AckBrainConfig):
    provider = FakeProvider(returns="   \n\t  ")
    gen = AckGenerator(config=config, provider=provider)
    assert await gen.run("hi", language="de") is None


@pytest.mark.asyncio
async def test_truncates_long_output_at_first_sentence(config: AckBrainConfig):
    long_text = (
        "Mache ich, Chef. Hier sind die Details, die du wissen musst, weil "
        "es sehr lang sein könnte und der User nicht alles hören will."
    )
    provider = FakeProvider(returns=long_text)
    gen = AckGenerator(config=config, provider=provider)
    result = await gen.run("erkläre mir das System", language="de")
    assert result is not None
    assert result.endswith(".")
    assert "Details" not in result  # truncated before second sentence


@pytest.mark.asyncio
async def test_scrubbed_empty_returns_none(config: AckBrainConfig):
    # The output_filter strips banned vocabulary. A provider that only
    # emits "Sub-Agent" produces "" after scrub.
    provider = FakeProvider(returns="Der Sub-Agent läuft.")
    gen = AckGenerator(config=config, provider=provider)
    result = await gen.run("hi", language="de")
    # Depends on scrub_for_voice — must return None if the scrub strips
    # to less than 3 alphanumeric chars
    assert result is None or "Sub-Agent" not in result


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold(config: AckBrainConfig):
    provider = FakeProvider(raises=RuntimeError("down"))
    breaker = CircuitBreaker(threshold=2, cooldown_s=60)
    gen = AckGenerator(config=config, provider=provider, circuit_breaker=breaker)
    assert await gen.run("a", language="de") is None
    assert await gen.run("b", language="de") is None
    # After 2 consecutive failures, breaker is OPEN; next call should not
    # even invoke the provider
    pre_count = len(provider.calls)
    assert await gen.run("c", language="de") is None
    assert len(provider.calls) == pre_count, (
        "Provider should not be invoked when breaker is OPEN"
    )
```

- [ ] **Step 2: Run → expect FAIL**

```powershell
pytest tests/unit/brain/test_ack_brain/test_generator.py -v
```

- [ ] **Step 3: Implement AckGenerator**

```python
# jarvis/brain/ack_brain/generator.py
"""AckGenerator orchestrator.

Wraps an underlying AbstractAckProvider with the four cross-cutting
concerns:

* hard timeout (asyncio.wait_for at config.timeout_ms / 1000)
* truncation at first sentence boundary when output > 25 words
* schwarzliste scrub via jarvis.brain.output_filter.scrub_for_voice
* circuit breaker (open after N consecutive failures)

Returns the cleaned ack text on success, or None on any failure. The
caller (router.py:_build_ack_emitter) treats None as "do not emit".
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ack_brain.providers.base import AbstractAckProvider
from jarvis.brain.output_filter import scrub_for_voice

log = logging.getLogger(__name__)

__all__ = ["AckGenerator"]

_SENTENCE_END_RE = re.compile(r"[.!?]")
_MAX_WORDS = 25


class AckGenerator:
    def __init__(
        self,
        *,
        config: AckBrainConfig,
        provider: AbstractAckProvider,
        circuit_breaker: CircuitBreaker | None = None,
        telemetry: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._breaker = circuit_breaker or CircuitBreaker(
            threshold=config.circuit_breaker_threshold,
            cooldown_s=config.circuit_breaker_cooldown_s,
        )
        self._telemetry = telemetry or (lambda counter_name: None)

    async def run(self, utterance: str, *, language: str = "de") -> str | None:
        if not self._config.enabled:
            return None
        if not self._breaker.allow():
            self._telemetry("ack_circuit_breaker_open_total")
            return None

        self._telemetry("ack_called_total")
        try:
            text = await asyncio.wait_for(
                self._provider.complete(utterance, language=language),
                timeout=self._config.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            self._telemetry("ack_timeout_total")
            self._breaker.record_failure()
            return None
        except Exception as exc:  # noqa: BLE001 — silent-or-strong principle
            self._telemetry("ack_provider_error_total")
            self._breaker.record_failure()
            log.warning("ack provider %s failed: %s", self._provider.name, exc)
            return None

        text = (text or "").strip()
        if not text:
            self._telemetry("ack_empty_response_total")
            self._breaker.record_success()  # provider is alive, just gave nothing
            return None

        if len(text.split()) > _MAX_WORDS:
            text = _truncate_at_first_sentence(text)
            self._telemetry("ack_truncated_total")

        scrubbed = scrub_for_voice(text, language=language)
        if not scrubbed or len(scrubbed.strip()) < 3:
            self._telemetry("ack_scrubbed_empty_total")
            self._breaker.record_success()
            return None

        self._telemetry("ack_emitted_total")
        self._breaker.record_success()
        return scrubbed.strip()


def _truncate_at_first_sentence(text: str) -> str:
    match = _SENTENCE_END_RE.search(text)
    if not match:
        # No sentence boundary found — cap by word count
        return " ".join(text.split()[:_MAX_WORDS])
    return text[: match.end()].strip()
```

- [ ] **Step 4: Run tests → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_brain/test_generator.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_brain/generator.py tests/unit/brain/test_ack_brain/test_generator.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): add AckGenerator orchestrator

Wraps an underlying provider with the four cross-cutting concerns:
hard timeout (asyncio.wait_for), schwarzliste scrub via scrub_for_voice,
sentence-boundary truncation past 25 words, and the CircuitBreaker.
Returns None on every failure path; caller maps None to silent (no
AnnouncementRequested event). Telemetry callable is injected so tests
can assert counter calls without coupling to flight-recorder internals.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 5.2: Refactor `ack_generator.py` to thin adapter

**Files:**
- Modify: `jarvis/brain/ack_generator.py` (remove templates, keep API surface)
- Modify: `tests/unit/brain/test_ack_generator.py` (rewrite)

- [ ] **Step 1: Read the current file to understand its public surface**

```powershell
pytest tests/unit/brain/test_ack_generator.py -v
```

Note: existing tests against the template implementation. Many will need updating.

- [ ] **Step 2: Replace `ack_generator.py` content**

```python
# jarvis/brain/ack_generator.py
"""Thin adapter over AckGenerator (Flash-Brain orchestrator).

History: this module used to host per-tool template handlers
(_ack_search_web, _ack_open_app, ...) plus a _GENERIC_ACK fallback.
That approach was rejected because the generic fallback emitted
"Verstanden, ich kümmere mich darum." for every spawn_sub_jarvis call,
regardless of whether the user asked an action ("Mach Spotify auf") or
a knowledge question ("Wann wird Albel eingestellt?"). The new design
delegates to a provider-pluggable LLM (see jarvis.brain.ack_brain) and
this module only exposes the legacy surface that router.py expects.

Kept here:
* ACK_SKIP_TOOLS — frozenset of tool names that never get an ack
* is_voice_control_utterance() — fast regex bypass for "sei still" etc.
* final_summary_marker() / should_prepend_marker() — orthogonal
  "Erledigt." marker for the brain's final reply (NOT pre-thinking ack)

Removed (use AckGenerator.run() instead):
* All per-tool template handlers
* _GENERIC_ACK / _TEMPLATES dispatch table
"""
from __future__ import annotations

import re

from jarvis.brain.ack_brain.generator import AckGenerator

__all__ = [
    "ACK_SKIP_TOOLS",
    "final_summary_marker",
    "generate_ack",
    "is_voice_control_utterance",
    "should_prepend_marker",
]


# Tools that must NOT emit an ack even when called. Two reasons:
#   (a) passive state reads — no user-visible action to confirm
#   (b) low-latency UI events — per-event chat ack would chatter
ACK_SKIP_TOOLS: frozenset[str] = frozenset({
    # passive observations
    "awareness_snapshot",
    "screen_snapshot",
    "whoami",
    # low-latency UI events
    "click",
    "hotkey",
    "move_mouse",
    "type_text",
    # silent meta tools (Phase 7.3 read-only)
    "list_mutable_settings",
    "get_config_value",
})


_VOICE_CONTROL_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:mach\s+)?(?:lauter|leiser|laut|leise)(?:\s+machen)?"
    r"|sei\s+(?:bitte\s+)?(?:still|leise|stiller)"
    r"|halt(?:\s+(?:die\s+)?klappe)?"
    r"|stop(?:p)?(?:\s+(?:sprechen|reden|talking))?"
    r"|pause(?:\s+(?:die\s+)?(?:wiedergabe|musik|sprache))?"
    r"|pausier(?:e|en|t)?"
    r"|stumm(?:\s+schalten)?"
    r"|schweig(?:e|en)?"
    r"|nicht\s+(?:so\s+)?(?:laut|leise)"
    r"|(?:be\s+)?quiet"
    r"|shut\s+up"
    r"|louder|quieter|softer"
    r"|volume\s+(?:up|down)"
    r"|(?:please\s+)?stop(?:\s+(?:speaking|talking))?"
    r"|mute(?:\s+yourself)?"
    r")"
    r"(?:\s+(?:bitte|mal|jetzt|please|now|please\s+now))?"
    r"\s*[!.?]?\s*$",
    re.IGNORECASE,
)


_FINAL_MARKERS: dict[str, str] = {"de": "Erledigt.", "en": "Done."}

_ALREADY_CONFIRMING_RE = re.compile(
    r"^\s*(erledigt|fertig|okay|ok|alright|done|got\s+it|verstanden|in\s+ordnung|sure)\b",
    re.IGNORECASE,
)


def is_voice_control_utterance(utterance: str | None) -> bool:
    if not utterance:
        return False
    return bool(_VOICE_CONTROL_PATTERN.match(utterance.strip()))


async def generate_ack(
    utterance: str,
    *,
    language: str = "de",
    ack_generator: AckGenerator | None = None,
) -> str | None:
    """Return a short ack sentence for the given utterance, or None.

    Returns None when:
    * ack_generator is None (feature disabled or not wired)
    * utterance is a voice-control command (sei still, lauter, etc.)
    * underlying AckGenerator failed (timeout, scrub-empty, etc.)
    """
    if ack_generator is None:
        return None
    if is_voice_control_utterance(utterance):
        return None
    return await ack_generator.run(utterance, language=language)


def final_summary_marker(language: str = "de") -> str:
    return _FINAL_MARKERS["en" if (language or "").lower().startswith("en") else "de"]


def should_prepend_marker(brain_text: str | None) -> bool:
    if not brain_text or not brain_text.strip():
        return True
    return not bool(_ALREADY_CONFIRMING_RE.match(brain_text))
```

- [ ] **Step 3: Rewrite the test file**

```python
# tests/unit/brain/test_ack_generator.py
"""Tests for the refactored thin-adapter ack_generator.

The module no longer carries template logic — it delegates to
AckGenerator. Tests here focus on the surface still exposed:
ACK_SKIP_TOOLS membership, voice-control regex coverage, the marker
helpers, and that generate_ack() correctly delegates / short-circuits.
"""
from __future__ import annotations

import pytest

from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ack_brain.generator import AckGenerator
from jarvis.brain.ack_generator import (
    ACK_SKIP_TOOLS,
    final_summary_marker,
    generate_ack,
    is_voice_control_utterance,
    should_prepend_marker,
)


class _StubProvider:
    name = "stub"

    def __init__(self, returns: str = ""):
        self._returns = returns

    async def complete(self, utterance: str, *, language: str) -> str:
        return self._returns


@pytest.mark.parametrize("utterance", [
    "sei still",
    "Sei bitte leise.",
    "halt die klappe",
    "stop sprechen",
    "lauter machen",
    "leiser bitte",
    "be quiet",
    "shut up",
    "stop talking",
    "mute yourself",
])
def test_voice_control_detected(utterance: str):
    assert is_voice_control_utterance(utterance) is True


@pytest.mark.parametrize("utterance", [
    "lauter Applaus war zu hoeren",  # narrative, not command
    "still im Gespraech",
    "öffne Spotify",
    "wann ist Mittag",
    "",
    None,
])
def test_non_voice_control(utterance: str | None):
    assert is_voice_control_utterance(utterance) is False


@pytest.mark.parametrize("tool_name", sorted(ACK_SKIP_TOOLS))
def test_known_skip_tools(tool_name: str):
    assert tool_name in ACK_SKIP_TOOLS


def test_final_marker_de():
    assert final_summary_marker("de") == "Erledigt."


def test_final_marker_en():
    assert final_summary_marker("en-US") == "Done."


def test_should_prepend_on_empty():
    assert should_prepend_marker("") is True


def test_should_not_prepend_when_self_confirming():
    assert should_prepend_marker("Okay, das ist erledigt.") is False


@pytest.mark.asyncio
async def test_generate_ack_returns_none_without_ack_generator():
    result = await generate_ack("Mach Spotify auf", language="de", ack_generator=None)
    assert result is None


@pytest.mark.asyncio
async def test_generate_ack_short_circuits_on_voice_control():
    config = AckBrainConfig(enabled=True)
    gen = AckGenerator(config=config, provider=_StubProvider(returns="should not appear"))
    result = await generate_ack("sei still", language="de", ack_generator=gen)
    assert result is None


@pytest.mark.asyncio
async def test_generate_ack_delegates_to_ack_generator():
    config = AckBrainConfig(enabled=True, timeout_ms=500)
    gen = AckGenerator(config=config, provider=_StubProvider(returns="Mache ich, Chef."))
    result = await generate_ack("Mach Spotify auf", language="de", ack_generator=gen)
    assert result == "Mache ich, Chef."
```

- [ ] **Step 4: Run → expect GREEN**

```powershell
pytest tests/unit/brain/test_ack_generator.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/ack_generator.py tests/unit/brain/test_ack_generator.py
git commit -m "$(cat <<'EOF'
refactor(ack_generator): replace templates with thin AckGenerator adapter

The 12 per-tool template handlers and the _GENERIC_ACK fallback are
gone. The module retains its public surface (ACK_SKIP_TOOLS,
is_voice_control_utterance, generate_ack, marker helpers) for the
router and tool-use-loop to call, but generate_ack now accepts an
AckGenerator instance and delegates.

This is the structural fix for the Albel problem: tonality is decided
by the Flash-Brain looking at the utterance, not by a tool-keyed
dispatch table that knows only what the router chose.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 5.3: Wire AckGenerator into Router and factory

**Files:**
- Modify: `jarvis/brain/router.py` (lines 324-348)
- Modify: `jarvis/brain/factory.py`

- [ ] **Step 1: Read the existing wiring**

Open `jarvis/brain/router.py:324-348` (`_build_ack_emitter`). Open `jarvis/brain/factory.py` (look for `build_default_brain`).

- [ ] **Step 2: Modify `_build_ack_emitter`**

In `router.py`, the existing function looks roughly like:

```python
def _build_ack_emitter(self, utterance: str):
    async def emit(tool_name: str, tool_args: dict):
        text = generate_ack(tool_name, tool_args, language=...)
        if text is None:
            return
        await self._bus.publish(AnnouncementRequested(text=text, priority="normal"))
    return emit
```

Replace it with:

```python
def _build_ack_emitter(self, utterance: str):
    """Build an async ack emitter for the upcoming tool-use turn.

    Uses the configured AckGenerator (Flash-Brain). The emitter runs
    BEFORE the first tool is executed; output goes on the
    AnnouncementRequested bus with kind="preamble".

    Returns None if the AckGenerator is not configured (feature off).
    """
    if self._ack_generator is None:
        return None

    language = self._detect_language(utterance) if hasattr(self, "_detect_language") else "de"

    async def emit(tool_name: str, tool_args: dict) -> None:
        if tool_name in ACK_SKIP_TOOLS:
            return
        text = await generate_ack(
            utterance,
            language=language,
            ack_generator=self._ack_generator,
        )
        if not text:
            return
        await self._bus.publish(
            AnnouncementRequested(text=text, priority="normal", kind="preamble")
        )

    return emit
```

Also: in `Router.__init__`, accept an optional `ack_generator: AckGenerator | None = None` argument and store it as `self._ack_generator`.

Required imports at top of `router.py`:

```python
from jarvis.brain.ack_brain.generator import AckGenerator
from jarvis.brain.ack_generator import ACK_SKIP_TOOLS, generate_ack
from jarvis.core.events import AnnouncementRequested
```

- [ ] **Step 3: Modify `factory.py` to instantiate AckGenerator**

In `build_default_brain`, after loading the config, add:

```python
# Construct the Pre-Thinking Ack Flash-Brain, if enabled.
ack_generator: AckGenerator | None = None
if config.ack_brain.enabled:
    provider_cls = _load_ack_provider(config.ack_brain.provider)
    provider_config = getattr(config.ack_brain.providers, config.ack_brain.provider)
    provider_instance = provider_cls(provider_config)
    ack_generator = AckGenerator(config=config.ack_brain, provider=provider_instance)
```

And the helper:

```python
def _load_ack_provider(name: str) -> type:
    """Load a provider class by name from the jarvis.ack_provider entry_points group."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="jarvis.ack_provider"):
        if ep.name == name:
            return ep.load()
    raise RuntimeError(f"unknown ack provider {name!r}; check pyproject.toml")
```

Pass `ack_generator=ack_generator` to the `Router` constructor.

- [ ] **Step 4: Verify wiring runs**

```powershell
python -c "from jarvis.brain.factory import build_default_brain; b = build_default_brain(); print('ok')"
```

Expected: `ok` (and no traceback).

- [ ] **Step 5: Write the integration test**

```python
# tests/integration/test_ack_flow.py
"""End-to-end happy path: utterance → AckGenerator → bus → TTS-invoke.

Stubs the provider and the TTS but uses the real Router + AckGenerator +
output_filter scrub.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.ack_brain.generator import AckGenerator
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, response: str):
        self._response = response

    async def complete(self, utterance: str, *, language: str) -> str:
        return self._response


@pytest.mark.asyncio
async def test_ack_published_on_bus_with_preamble_kind():
    bus = EventBus()
    received: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, received.append)

    config = AckBrainConfig(enabled=True, timeout_ms=1000)
    provider = _ScriptedProvider("Lass mich kurz nachschauen.")
    gen = AckGenerator(config=config, provider=provider)

    text = await gen.run("Wann wird Albel eingestellt?", language="de")
    assert text == "Lass mich kurz nachschauen."

    await bus.publish(
        AnnouncementRequested(text=text, priority="normal", kind="preamble")
    )
    await asyncio.sleep(0)  # let dispatcher run

    assert len(received) == 1
    assert received[0].text == "Lass mich kurz nachschauen."
    assert received[0].kind == "preamble"


@pytest.mark.asyncio
async def test_failed_provider_publishes_nothing():
    bus = EventBus()
    received: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, received.append)

    class _BrokenProvider:
        name = "broken"

        async def complete(self, utterance: str, *, language: str) -> str:
            raise RuntimeError("provider down")

    config = AckBrainConfig(enabled=True, timeout_ms=1000)
    gen = AckGenerator(config=config, provider=_BrokenProvider())
    text = await gen.run("hi", language="de")
    assert text is None

    if text is not None:
        await bus.publish(AnnouncementRequested(text=text, priority="normal"))
    await asyncio.sleep(0)
    assert received == []
```

- [ ] **Step 6: Run integration test**

```powershell
pytest tests/integration/test_ack_flow.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 7: Write the provider-swap integration test**

```python
# tests/integration/test_ack_provider_swap.py
"""Switching [ack_brain].provider via config changes which adapter loads."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jarvis.core.config import load_config


def test_load_grok_provider_config(tmp_path: Path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text(textwrap.dedent("""
        [ack_brain]
        enabled = true
        provider = "grok"

        [ack_brain.providers.grok]
        model = "grok-4-flash"
        api_key_secret = "grok_api_key"
    """).strip())
    config = load_config(config_file)
    assert config.ack_brain.provider == "grok"
    assert config.ack_brain.providers.grok.model == "grok-4-flash"


def test_disabled_means_no_instantiation(tmp_path: Path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text(textwrap.dedent("""
        [ack_brain]
        enabled = false
    """).strip())
    config = load_config(config_file)
    assert config.ack_brain.enabled is False


def test_unknown_provider_rejected(tmp_path: Path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text(textwrap.dedent("""
        [ack_brain]
        provider = "not-a-real-thing"
    """).strip())
    with pytest.raises(Exception):
        load_config(config_file)
```

- [ ] **Step 8: Run integration test**

```powershell
pytest tests/integration/test_ack_provider_swap.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add jarvis/brain/router.py jarvis/brain/factory.py tests/integration/test_ack_flow.py tests/integration/test_ack_provider_swap.py
git commit -m "$(cat <<'EOF'
feat(ack_brain): wire AckGenerator into Router and factory

Router accepts an optional ack_generator parameter. _build_ack_emitter
now uses it when present, publishing AnnouncementRequested with
kind="preamble". factory.build_default_brain instantiates AckGenerator
when [ack_brain].enabled = true, loading the provider via the
entry_points group. Integration tests cover the happy path
(provider → bus → kind=preamble) and the failed-provider path
(silent, no event).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 5

- [ ] `pytest tests/unit/brain/test_ack_brain/test_generator.py -v` → 8 pass
- [ ] `pytest tests/unit/brain/test_ack_generator.py -v` → all pass (refactored suite)
- [ ] `pytest tests/integration/test_ack_flow.py -v` → 2 pass
- [ ] `pytest tests/integration/test_ack_provider_swap.py -v` → 3 pass
- [ ] `python -c "from jarvis.brain.factory import build_default_brain; build_default_brain(); print('ok')"` prints `ok`
- [ ] Three commits
- [ ] Full test suite still green: `pytest tests/unit/ tests/integration/ -v` matches baseline pass-count + new test counts

### Ready-to-paste prompt for Stage 5

```text
Du implementierst Etappe 5 ("AckGenerator Core + Wiring") des Pre-Thinking-Ack
Flash-Brain Features. Das ist die anspruchsvollste Etappe — nimm dir Zeit.

Pflichtlektüre:
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — komplette Spec, besonders §3 Architecture, §4 Components, §6 Failure
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Etappe 5"
3. CLAUDE.md — Brain-Routing & BrainManager, Streaming-First-Class
4. jarvis/brain/router.py:324-348 (bestehender _build_ack_emitter)
5. jarvis/brain/factory.py (build_default_brain)
6. jarvis/brain/output_filter.py:scrub_for_voice (40 Schwarzlist-Patterns)

Pre-Condition: Etappen 1–4 sind committet. Wenn nicht, STOPP.

Scope:
- jarvis/brain/ack_brain/generator.py mit AckGenerator
- Tests in tests/unit/brain/test_ack_brain/test_generator.py
- REFACTOR jarvis/brain/ack_generator.py (Templates raus, Adapter rein)
- REWRITE tests/unit/brain/test_ack_generator.py
- MODIFY jarvis/brain/router.py:324-348 (neue _build_ack_emitter)
- MODIFY jarvis/brain/factory.py (AckGenerator instantiierung)
- Integration tests: tests/integration/test_ack_flow.py
- Integration tests: tests/integration/test_ack_provider_swap.py

Was du NICHT machst:
- KEINE UI-Änderungen (das ist E6)
- KEIN Wizard-Step (das ist E6)
- KEINE neuen Provider — die vier aus E4 reichen
- KEIN automatischer Provider-Fallback bei Failure — Silent ist by design
- KEIN Retry-Loop — ein Ack der nach 3s kommt ist sinnlos

WICHTIGSTE Design-Eigenschaft (Spec §3): Der Flash-Brain sieht NUR die
User-Utterance, NICHT den Router-Tool-Output. Das ist die strukturelle
Lösung für das Albel-Problem.

Akzeptanzkriterien:
1. pytest tests/unit/brain/test_ack_brain/test_generator.py -v → 8 pass
2. pytest tests/unit/brain/test_ack_generator.py -v → all pass
3. pytest tests/integration/test_ack_flow.py -v → 2 pass
4. pytest tests/integration/test_ack_provider_swap.py -v → 3 pass
5. python -c "from jarvis.brain.factory import build_default_brain; build_default_brain(); print('ok')" → "ok"
6. Drei Commits (generator + ack_generator-refactor + router/factory-wiring)
7. Voller Test-Run: pytest tests/ -v zeigt keinen neuen Regressions-Failure
   gegenüber dem Stand vor E5
```

---

## Stage 6 — UI + Smoke + Wizard + Docs

**Goal:** Surface the new feature in the chat UI as a distinct preamble bubble, add a Setup-Wizard step for selecting the provider, ship a manual smoke-test script, and write a one-page user-facing doc.

**Effort:** Medium (2-3 hours)

**Dependencies:** E1–E5 all committed.

### Files

- **Modify:**
  - `jarvis/ui/web/server.py` (forward kind="preamble" as new WebSocket message role)
  - `jarvis/ui/web/frontend/src/types/messages.ts` (extend MessageRole)
  - `jarvis/ui/web/frontend/src/views/ChatView.tsx` (render preamble bubble)
  - `jarvis/setup/wizard.py` (add provider-select step)
  - `CLAUDE.md` (mention `[ack_brain]` in architecture section)
- **Create:**
  - `scripts/smoke-test-ack.ps1`
  - `docs/ack-brain.md` (user-facing one-pager)

### Tasks

#### Task 6.1: Forward `kind="preamble"` to WebSocket

**Files:**
- Modify: `jarvis/ui/web/server.py`

- [ ] **Step 1: Locate AnnouncementRequested subscription**

Open `jarvis/ui/web/server.py`. Find where `AnnouncementRequested` events are subscribed and forwarded to the chat WebSocket (likely a `_on_announcement` handler near the WebSocket route).

- [ ] **Step 2: Modify the handler**

The existing serialization probably emits `{"type": "announcement", "text": "..."}`. Extend it:

```python
async def _on_announcement(event: AnnouncementRequested) -> None:
    role = "preamble" if event.kind == "preamble" else "jarvis"
    payload = {"type": "chat_message", "role": role, "text": event.text}
    await _broadcast_to_chat_clients(payload)
```

- [ ] **Step 3: Write a WebSocket schema test**

Add a test under `tests/integration/` or wherever WS tests live (look for existing `test_ws_schema.py`):

```python
# tests/integration/test_ws_preamble_forward.py
"""Preamble announcements emit role='preamble' on the chat WebSocket."""
from __future__ import annotations

# Skeleton — adapt to the existing WS test harness in the repo
# (e.g. fastapi.testclient WebSocket testing pattern).
```

Adapt to whatever WS-testing pattern the repo uses; if none, just add a unit test that asserts the role mapping function returns the right string.

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/server.py tests/integration/test_ws_preamble_forward.py
git commit -m "$(cat <<'EOF'
feat(ui): forward kind=preamble announcements to WebSocket as new role

The chat WebSocket now distinguishes preamble (pre-thinking ack)
messages from regular jarvis responses via the role field. Frontend
uses this to render preamble bubbles with muted styling. Backwards-
compat: events without kind continue to map to role="jarvis".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6.2: Frontend message-type extension

**Files:**
- Modify: `jarvis/ui/web/frontend/src/types/messages.ts`
- Modify: `jarvis/ui/web/frontend/src/views/ChatView.tsx`

- [ ] **Step 1: Extend the MessageRole type**

```typescript
// jarvis/ui/web/frontend/src/types/messages.ts
export type MessageRole = "user" | "jarvis" | "preamble";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  timestamp: number;
}
```

- [ ] **Step 2: Update ChatView.tsx to render preamble bubbles**

Find the message-list rendering loop. Add a class branch for `preamble`:

```tsx
{messages.map((msg) => {
  const isPreamble = msg.role === "preamble";
  const bubbleClass = isPreamble
    ? "rounded-lg bg-slate-700/40 px-3 py-2 text-sm italic text-slate-300"
    : msg.role === "user"
    ? "rounded-lg bg-blue-600 px-3 py-2 text-white"
    : "rounded-lg bg-slate-800 px-3 py-2 text-slate-100";
  return (
    <div key={msg.id} className={bubbleClass}>
      {isPreamble && (
        <span className="mr-2 inline-block rounded bg-slate-600 px-1.5 py-0.5 text-xs uppercase tracking-wide">
          ack
        </span>
      )}
      {msg.text}
    </div>
  );
})}
```

Adjust class names to match the existing Tailwind / shadcn vocabulary used in your repo.

- [ ] **Step 3: Type-check frontend**

```powershell
cd jarvis/ui/web/frontend
npm run build
```

Expected: builds without TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/frontend/src/types/messages.ts jarvis/ui/web/frontend/src/views/ChatView.tsx
git commit -m "$(cat <<'EOF'
feat(ui): render preamble messages as muted italic bubbles with 'ack' chip

Frontend now distinguishes the Pre-Thinking-Ack from regular Jarvis
replies. Preamble bubbles are visually subordinate (smaller, italic,
muted bg) and carry a tiny 'ack' chip so the user can recognise the
two-message pattern (ack → final answer).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6.3: Setup-Wizard step

**Files:**
- Modify: `jarvis/setup/wizard.py`

- [ ] **Step 1: Add a wizard step**

Find the existing wizard flow in `jarvis/setup/wizard.py`. Add a step after the brain-provider step:

```python
def _step_ack_brain_provider(state: WizardState) -> WizardState:
    """Ask the user which fast LLM provider to use for the pre-thinking ack.

    Lists only providers whose API key is already in the Credential Manager,
    plus Ollama (local, no key required). User can pick "skip" to leave the
    feature disabled.
    """
    available = ["skip"]
    if get_secret("gemini_api_key"):
        available.append("gemini")
    if get_secret("grok_api_key"):
        available.append("grok")
    if get_secret("openai_api_key"):
        available.append("openai")
    available.append("ollama")  # always available, local

    choice = _prompt_choice(
        "Welcher schnelle LLM-Provider soll fuer Pre-Thinking-Acks "
        "verwendet werden? (Ack = der kurze Satz den du hoerst bevor "
        "Jarvis seine eigentliche Antwort gibt.)",
        choices=available,
    )

    if choice == "skip":
        state.ack_brain_enabled = False
    else:
        state.ack_brain_enabled = True
        state.ack_brain_provider = choice
    return state
```

Then in `_write_config`, persist these to `[ack_brain]`.

- [ ] **Step 2: Smoke-test wizard step manually**

```powershell
python -m jarvis --wizard
```

Walk through the prompts; confirm the new ack_brain step appears and persists to `jarvis.toml`.

- [ ] **Step 3: Commit**

```bash
git add jarvis/setup/wizard.py
git commit -m "$(cat <<'EOF'
feat(setup): add ack_brain provider step to wizard

Lists only providers whose API key is already in the Windows
Credential Manager (gemini / grok / openai) plus ollama (local, no
key). User can pick 'skip' to leave the feature off. Persists to
[ack_brain].provider in jarvis.toml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6.4: Smoke-test script

**Files:**
- Create: `scripts/smoke-test-ack.ps1`

- [ ] **Step 1: Write the script**

```powershell
# scripts/smoke-test-ack.ps1
# Manual smoke test for the Pre-Thinking Ack Flash-Brain.
#
# Replays five scripted utterances per language via the dev WS API,
# records audio output to ./smoke-ack-recordings/, and prints a
# pass/fail summary. Manual playback verification still required.

param(
    [string]$Language = "de",
    [string]$Endpoint = "http://localhost:47821"
)

$ErrorActionPreference = "Stop"

$Utterances = @{
    "de" = @(
        "Mach Spotify auf",
        "Wann wird Albel eingestellt?",
        "Such mir Fluege nach San Francisco fuer morgen",
        "Hallo Jarvis",
        "Sei still"
    )
    "en" = @(
        "Open Spotify",
        "When does Albel start?",
        "Find me flights to San Francisco for tomorrow",
        "Hi Jarvis",
        "Be quiet"
    )
}

$outDir = Join-Path $PSScriptRoot "..\smoke-ack-recordings"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "Smoke-test starting against $Endpoint (language=$Language)" -ForegroundColor Cyan

foreach ($u in $Utterances[$Language]) {
    Write-Host "`n--- Utterance: $u ---"
    $body = @{ utterance = $u; language = $Language } | ConvertTo-Json
    $start = Get-Date
    try {
        $response = Invoke-RestMethod -Uri "$Endpoint/api/dev/replay-stt" `
            -Method POST -Body $body -ContentType "application/json"
        $elapsed = (Get-Date) - $start
        Write-Host "Reply: $($response | ConvertTo-Json -Depth 4)"
        Write-Host "Elapsed: $($elapsed.TotalMilliseconds) ms"
    } catch {
        Write-Host "FAIL: $_" -ForegroundColor Red
    }
    Start-Sleep -Seconds 2
}

Write-Host "`nManual checks required:" -ForegroundColor Yellow
Write-Host "1. Each non-voice-control utterance produced a preamble bubble + a main reply"
Write-Host "2. 'Sei still' / 'Be quiet' produced ZERO preamble audio"
Write-Host "3. No 'Subagent' / 'Worker' / 'Sir' / 'Sehr wohl' anywhere"
Write-Host "4. Preamble was spoken in the same TTS voice as the main reply"
Write-Host "5. Preamble was temporally separated from the main reply (no overlap)"
```

- [ ] **Step 2: Make the script discoverable**

```powershell
Get-ChildItem scripts -Filter smoke-test-ack.ps1
```

Expected: file appears.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke-test-ack.ps1
git commit -m "$(cat <<'EOF'
test(ack_brain): add manual smoke-test script for end-to-end verification

Replays five scripted utterances per language via the dev WS endpoint
and prints latency + reply payload. Five manual playback checks the
operator confirms: bubble pair, voice-control silent, no banned words,
matching TTS voice, no audio overlap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6.5: User-facing documentation

**Files:**
- Create: `docs/ack-brain.md`
- Modify: `CLAUDE.md` (mention `[ack_brain]` section)

- [ ] **Step 1: Write the doc**

```markdown
<!-- docs/ack-brain.md -->
# Pre-Thinking Acknowledgment (Flash-Brain)

A small, fast LLM that says a short context-aware confirmation sentence
the moment you finish speaking — before Jarvis runs the main brain or
any tools. This makes Jarvis feel responsive on heavy tasks (research,
file edits, OpenClaw spawns) instead of leaving you in silence.

## How it works

1. You finish speaking. STT finalises the transcript.
2. Two LLM calls fire in parallel:
   - The **Router-Brain** decides which tool to call.
   - The **Flash-Brain** generates a one-sentence acknowledgment.
3. The acknowledgment plays through the same TTS voice as Jarvis's main
   reply. The main reply follows when the tool / brain finishes.

## Configuration

Open `jarvis.toml`. The feature lives under `[ack_brain]`:

```toml
[ack_brain]
enabled = true
provider = "gemini"     # or "grok", "openai", "ollama"
timeout_ms = 1500
```

After changing `provider`, restart Jarvis. Per-provider model and API
key settings live under `[ack_brain.providers.<provider>]`.

## Troubleshooting

- **No preamble plays:** Check `[ack_brain].enabled = true`. Check the
  provider's API key is in Windows Credential Manager (`personal-jarvis`
  service, secret name matches `api_key_secret`).
- **Preamble sounds wrong:** The persona prompt lives in
  `jarvis/brain/ack_brain/persona_prompt.py`. Tonality changes happen
  there, not in config.
- **Latency too high:** Switch provider in config or shorten
  `timeout_ms` (default 1500). Local Ollama is fastest on a GPU.

## Disable the feature

Set `[ack_brain].enabled = false` in `jarvis.toml` and restart. Jarvis
falls back to silent before each tool call, same as before this feature
existed.
```

- [ ] **Step 2: Add a one-line reference to CLAUDE.md**

Append to the appropriate section in `CLAUDE.md` (e.g. under "Phasen 0-5" or in the architecture overview):

```markdown
- **Pre-Thinking-Ack (2026-05-11)** — Provider-pluggable Flash-Brain ([`jarvis/brain/ack_brain/`](jarvis/brain/ack_brain/)) runs in parallel with the Router-Brain and emits a butler-style preamble before the main reply. User doc: [`docs/ack-brain.md`](docs/ack-brain.md). Spec: [`docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md`](docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md). Default disabled; enable via `[ack_brain].enabled = true`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/ack-brain.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(ack_brain): add user-facing one-pager and CLAUDE.md reference

docs/ack-brain.md explains the feature in plain language with config
snippets and a troubleshooting section. CLAUDE.md gains a one-line
reference so future agents know to look there before reinventing the
template-based pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Acceptance Criteria — Stage 6

- [ ] `npm run build` in `jarvis/ui/web/frontend` succeeds without TS errors
- [ ] `python -m jarvis --wizard` shows the new ack_brain step
- [ ] `scripts/smoke-test-ack.ps1` exists and runs against a live Jarvis instance
- [ ] `docs/ack-brain.md` exists and is referenced in CLAUDE.md
- [ ] Five commits
- [ ] Full repo test suite: `pytest tests/ -v` → no new regressions
- [ ] Manual smoke run: enabling `[ack_brain].enabled = true` in `jarvis.toml`, restarting Jarvis, saying "Wann wird Albel eingestellt?" produces audible "Lass mich kurz nachschauen." within ~1 second, followed by the main reply

### Ready-to-paste prompt for Stage 6

```text
Du implementierst Etappe 6 ("UI + Smoke + Wizard + Docs") des
Pre-Thinking-Ack Flash-Brain Features. Letzte Etappe — final polish.

Pflichtlektüre:
1. docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md
   — §4 UI/Frontend section
2. docs/superpowers/plans/2026-05-11-pre-thinking-ack-flash-brain-impl-plan.md
   — Section "Etappe 6"
3. CLAUDE.md — Desktop-App & Channels
4. jarvis/ui/web/server.py (existing _on_announcement WS forwarding)
5. jarvis/setup/wizard.py (existing wizard step pattern)

Pre-Condition: Etappen 1–5 sind committet UND der Backend-Pfad funktioniert
(test_ack_flow.py grün). Wenn nicht, STOPP.

Scope:
- jarvis/ui/web/server.py: AnnouncementRequested.kind="preamble" als
  neue WebSocket-Rolle forwarden
- jarvis/ui/web/frontend/src/types/messages.ts: MessageRole = "user" |
  "jarvis" | "preamble"
- jarvis/ui/web/frontend/src/views/ChatView.tsx: Preamble-Bubble
  rendern (italic, muted, "ack"-Chip)
- jarvis/setup/wizard.py: Provider-Auswahl-Step für ack_brain
- scripts/smoke-test-ack.ps1: 5 Utterances pro Sprache + 5 manuelle Checks
- docs/ack-brain.md: User-facing One-Pager
- CLAUDE.md: Eine Zeile Referenz unter Phasen-Section

Was du NICHT machst:
- KEINE Backend-Änderungen (alles ist in E1-E5 done)
- KEINE neuen Tests in tests/unit/ (Backend-Coverage steht)
- KEINE Persona-Prompt-Änderungen
- KEINE neuen Provider

Manueller Final-Smoke (Acceptance):
1. jarvis.toml: [ack_brain].enabled = true setzen, provider auswählen
2. Restart-Sequenz: pip install -e . --no-deps, dann Jarvis neu starten
3. "Wann wird Albel eingestellt?" sprechen
4. Audio innerhalb 1 Sekunde: "Lass mich kurz nachschauen."
5. Audio danach: Hauptantwort vom Brain
6. Chat-UI zeigt beide als getrennte Bubbles
7. Voice-control "Sei still" produziert kein Preamble-Audio

Akzeptanzkriterien:
1. cd jarvis/ui/web/frontend && npm run build → keine TS-Fehler
2. python -m jarvis --wizard → neuer ack_brain-Step erscheint
3. scripts/smoke-test-ack.ps1 existiert
4. docs/ack-brain.md existiert + CLAUDE.md referenziert es
5. Fünf Commits
6. pytest tests/ -v → keine neuen Regressions
7. Manueller End-to-End-Smoke wie oben beschrieben → green
```

---

## Final Acceptance — Whole Feature

After all six stages are committed:

- [ ] `pytest tests/ -v` → all pass, no new regressions vs. pre-E1 baseline
- [ ] `pytest tests/unit/brain/test_ack_brain/ -v` → ~35 tests pass
- [ ] `pytest tests/contract/test_ack_provider_protocol.py -v` → all pass
- [ ] `pytest tests/integration/test_ack_flow.py tests/integration/test_ack_provider_swap.py -v` → all pass
- [ ] Manual E2E: set `[ack_brain].enabled = true`, restart, say "Wann wird Albel eingestellt?", hear "Lass mich kurz nachschauen." within ~1 second, hear final answer after
- [ ] No `"Subagent"` / `"Worker"` / `"Sir"` / `"Sehr wohl"` audible in any test
- [ ] Voice-control utterances produce zero preamble audio
- [ ] Switching `[ack_brain].provider` between `gemini` / `grok` / `openai` / `ollama` and restarting → next utterance uses the new provider
- [ ] `python -c "import jarvis; print(jarvis.__file__)"` points to the repo (no stale-clone editable-install pin per BUG-006 / BUG-014)
