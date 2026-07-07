# One Official Full Install + Fully Generic Wake Word — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every branded wake-word artifact (bundled `hey_jarvis`/`hey_rhasspy` models, brand map, upstream-brand probe, jarvis special-case) so the wake system is fully generic, and make the official install path install the full profile (`[full]` including `[local-voice]`).

**Architecture:** The wake resolver (`resolve_wake_plan`) loses its "pretrained openWakeWord model" branch; every phrase flows through user-trained `custom_onnx` → `vosk_kws` (base) → `stt_match` (local Whisper) → honest hotkey degrade. The OpenWakeWord provider keeps running user-trained custom models via the word-agnostic melspec/embedding backbones but can no longer load or auto-download any named model. Packaging-wise `[full]` gains `local-voice` (which gains `faster-whisper`, drops `pvporcupine`), and the installer installs `.[full]` by default.

**Tech Stack:** Python 3.12 repo venv (`.venv\Scripts\python.exe`), pytest (asyncio_mode=auto, fakes not mocks), ruff, setuptools/pyproject packaging, uv lockfile.

**Spec:** `docs/superpowers/specs/2026-07-07-one-full-install-generic-wake-design.md` (approved 2026-07-07).

## Global Constraints

- English-only artifacts (CLAUDE.md §1) — all code/comments/docs/commit messages English.
- Shared working tree: stage ONLY files this plan touches, by explicit path. Never `git add -A`/`-u`/`.`.
- Base install stays torch-free: `requirements.in` / `requirements.txt` / `[project].dependencies` must NOT change. Only extras change.
- `WAKE_ENGINES` tuple must NOT change (TS parity test `tests/unit/speech/test_wake_engine_parity.py`).
- BUG-009 invariant survives: with phrase "Hey Jarvis", bare "jarvis" must NOT match.
- Honest degrade survives: unservable phrase → `engine="none"`, `wake_available=False`, hotkey message (`wake_phrase.py:515-540`) — do not touch that branch.
- Run tests from repo root via `.venv\Scripts\python.exe -m pytest ...`.
- Commit after each task (conventional message given per task).
- The Windows working tree is live for a running app — do NOT restart the app; code + tests only.

---

### Task 1: Packaging — `[full]` gains `local-voice`; `local-voice` gains `faster-whisper`, drops `pvporcupine`

**Files:**
- Modify: `pyproject.toml:144-153` (`full`), `pyproject.toml:233-263` (`local-voice` + comment)
- Modify: `jarvis/setup/wizard.py:195-202` (Porcupine SecretSpec), `jarvis/setup/wizard.py:399-403` (wake section blurb)
- Modify: `jarvis/hardware/detection.py:190` (drop `"pvporcupine",`)
- Modify: `jarvis/core/registry.py:8` (docstring example)
- Modify: `requirements.in:60-61` (stale comment), `.env.example:60` (Porcupine line)
- Test: `tests/unit/install/test_full_extra_contents.py` (create)

**Interfaces:**
- Produces: `[full] = personal-jarvis[desktop,telephony,channels,local-voice]`; `[local-voice] = silero-vad, webrtcvad-wheels, faster-whisper`. Task 2 (installer) and Task 8 (docs) rely on these exact extras.
- Note: `jarvis/core/config.py:153-157` (deprecated porcupine-era config keys) are KEPT — read-time back-compat pinned by `tests/unit/core/test_wake_word_config.py` (engine="porcupine" coerces to "auto"). Do not remove.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/install/test_full_extra_contents.py`:

```python
"""Packaging guards for the one-official-full-install decision (spec 2026-07-07).

[full] must carry local-voice so the advertised install path ships the local
Whisper wake/STT runtime; pvporcupine (dead, proprietary-keyed, branded
built-in keywords) must be gone from the dependency surface entirely.
"""
from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _extras() -> dict[str, list[str]]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_full_extra_includes_local_voice():
    full = " ".join(_extras()["full"])
    assert "local-voice" in full, "[full] must include the local-voice extra"


def test_local_voice_ships_faster_whisper():
    names = " ".join(_extras()["local-voice"])
    assert "faster-whisper" in names


def test_pvporcupine_is_gone_everywhere():
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    everything = list(data["project"]["dependencies"])
    for extra in data["project"]["optional-dependencies"].values():
        everything.extend(extra)
    assert not any("pvporcupine" in item for item in everything)
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/install/test_full_extra_contents.py -v`
Expected: `test_full_extra_includes_local_voice` and `test_local_voice_ships_faster_whisper` FAIL; `test_pvporcupine_is_gone_everywhere` FAIL (pvporcupine still present).

- [ ] **Step 3: Edit `pyproject.toml`**

Replace lines 144-153 (the `full` block + comment) with:

```toml
# "Everything" in one shot — the ONE advertised install profile (design spec
# 2026-07-07): desktop app + telephony + chat channels + local voice models.
# Each sub-extra carries its own platform markers, so `pip install .[full]`
# quietly skips what does not apply to the OS. The cloud-first BASE install
# (plain `pip install .`) remains the internal torch-free floor for CI and
# tiny headless servers; users are pointed at [full].
full = [
    "personal-jarvis[desktop,telephony,channels,local-voice]",
]
```

Replace lines 254-263 (tail of the `local-voice` comment + block) with:

```toml
# This extra therefore covers only heavier/alternate local paths: the
# torch-pulling silero-vad package, WebRTC VAD, and faster-whisper (the
# local-Whisper wake/STT accuracy path — previously pulled on demand by the
# in-app "enable local speech" installer, now preinstalled via [full] per the
# 2026-07-07 one-full-install decision; the in-app installer remains as the
# recovery path for base installs). pvporcupine was removed 2026-07-07: never
# imported anywhere, proprietary-keyed, ships branded built-in keywords.
local-voice = [
    "silero-vad>=5.1",
    "webrtcvad-wheels>=2.0",
    "faster-whisper>=1.0",
]
```

(Keep comment lines 233-253 as they are.)

- [ ] **Step 4: Clean the four dangling Porcupine references**

1. `jarvis/setup/wizard.py:195-202` — delete the whole `SecretSpec(... key="picovoice_access_key" ...)` entry from the specs list.
2. `jarvis/setup/wizard.py:399-403` — delete the `_Section("wake", ...)` entry whose blurb is "Optional. Only needed for the Porcupine wake engine." (and any now-empty handling the section list needs — check how `_SECTIONS` is consumed; if the "wake" section only existed for this secret, remove the section entirely).
3. `jarvis/hardware/detection.py:190` — delete the `"pvporcupine",` list element.
4. `jarvis/core/registry.py:8` — change the docstring example to a live optional dep, e.g. `"(e.g. discord.py only loads when the user enables the Discord channel)."`
5. `requirements.in:60-61` — rewrite the stale comment to say local-voice covers silero-vad/webrtcvad/faster-whisper (no Porcupine).
6. `.env.example:60` — delete the Picovoice/Porcupine line.

- [ ] **Step 5: Regenerate the uv lock (extras changed)**

Run: `uv lock` (from repo root; uv is the repo venv's tool). If uv is unavailable on PATH, try `.venv\Scripts\uv.exe lock`. Expected: `uv.lock` updates (pvporcupine pins gone, faster-whisper pinned). Do NOT touch `requirements.txt` (base unchanged — `uv pip compile` not needed).

- [ ] **Step 6: Run the test — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/install/test_full_extra_contents.py tests/unit/plugins/wake/test_wake_runtime_base_dep.py tests/unit/core/test_wake_word_config.py -v`
Expected: all PASS (base-dep guard and legacy-config coercion untouched).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock jarvis/setup/wizard.py jarvis/hardware/detection.py jarvis/core/registry.py requirements.in .env.example tests/unit/install/test_full_extra_contents.py
git commit -m "feat(packaging): [full] gains local-voice; drop dead pvporcupine, ship faster-whisper"
```

---

### Task 2: Installer installs `.[full]` by default

**Files:**
- Modify: `install/installer.py:197-247` (`step_pip_install`), `install/installer.py:447-476` (flags + auto-detection)
- Test: `tests/unit/install/test_installer_flow.py` (extend)

**Interfaces:**
- Consumes: Task 1's `[full]` definition.
- Produces: default install plan = editable no-deps + hash-pinned base (`requirements.txt`) + `pip install -e .[full]`; `--headless` = base floor only; `--with-voice-local` and `--with-desktop` become deprecated no-ops (accepted, warn, nothing extra).

- [ ] **Step 1: Write the failing test** (append to `tests/unit/install/test_installer_flow.py`, following that file's existing style for building/invoking the installer module — read it first and reuse its import/fixture pattern):

```python
def test_default_pip_plan_installs_full_extra(monkeypatch):
    """Design 2026-07-07: the one advertised install path installs .[full]."""
    import installer  # match the existing import pattern in this test file

    captured: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", lambda label, cmd, **kw: captured.append(list(cmd)) or 0)
    installer.step_pip_install(with_desktop=True, with_voice_local=False)
    joined = [" ".join(c) for c in captured]
    assert any(".[full]" in c for c in joined), joined
    assert not any(".[desktop]" in c for c in joined)
    assert not any(".[local-voice]" in c for c in joined)


def test_headless_pip_plan_stays_base_floor(monkeypatch):
    import installer

    captured: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", lambda label, cmd, **kw: captured.append(list(cmd)) or 0)
    installer.step_pip_install(with_desktop=False, with_voice_local=False)
    joined = [" ".join(c) for c in captured]
    assert not any(".[full]" in c for c in joined), joined
```

NOTE: adapt the monkeypatch target to the real helper name inside `step_pip_install` (read `install/installer.py:197-247` first — the plan-list is built as `plans = [...]`; if commands are executed via a different runner, patch that). The assertion contract is what matters: default → exactly one extras install and it is `.[full]`; headless → no extras.

- [ ] **Step 2: Run it — expect FAIL**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/install/test_installer_flow.py -v -k "full or floor"`
Expected: FAIL (`.[desktop]` in plan, no `.[full]`).

- [ ] **Step 3: Implement**

In `install/installer.py` `step_pip_install` (197-247): replace the conditional extras block

```python
if with_desktop:
    plans.append(("desktop extras", pip + ["install", "-e", ".[desktop]"]))
if with_voice_local:
    plans.append(("local-voice extras (Silero VAD, WebRTC VAD, Porcupine)",
                  pip + ["install", "-e", ".[local-voice]"]))
```

with:

```python
if with_desktop:
    # One official install profile (design 2026-07-07): desktop + telephony
    # + channels + local voice in one shot. Platform markers skip whatever
    # this OS cannot use. --headless keeps the torch-free base floor.
    plans.append(("full profile (desktop, telephony, channels, local voice)",
                  pip + ["install", "-e", ".[full]"]))
```

Keep the `with_desktop`/`with_voice_local` parameters (call sites + flags still pass them). In `main()` flag handling (447-466): mark `--with-voice-local` deprecated — accepted, prints one line `"--with-voice-local is deprecated: the full profile already includes local voice."`, sets nothing. Keep `--headless` semantics (with_desktop=False → base floor) and the OS auto-detection (470-476) unchanged.

- [ ] **Step 4: Run installer tests — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/install/ -v`
Expected: PASS (including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add install/installer.py tests/unit/install/test_installer_flow.py
git commit -m "feat(installer): default install is the full profile; deprecate --with-voice-local"
```

---

### Task 3: De-brand the wake resolver (delete the pretrained-model branch)

**Files:**
- Modify: `jarvis/speech/wake_constants.py` (delete `KNOWN_OWW_MODELS` L99-102, `_NON_WAKE_OWW_MODELS` L105, `match_known_oww_model` L189-210, `resolve_oww_model_path` L232-247 + helpers `_bundled_dir`/`_package_models_dir` L217-229 if no other consumer, `__all__` entries L308/315/316, docstring L15-20)
- Modify: `jarvis/speech/wake_phrase.py` (imports L36+L40, `_canonical_keyword` L256-260, resolve step 2 L422-441, module docstring L13-20, comment L317-320)
- Test: `tests/unit/speech/test_wake_phrase.py`, `tests/unit/speech/test_wake_custom_model.py`, `tests/unit/speech/test_pipeline_wake_plan.py`, `tests/unit/speech/test_wake_live_apply.py`, `tests/unit/ui/test_wake_word_route.py`

**Interfaces:**
- Produces: `resolve_wake_plan` chain = custom_onnx → vosk_kws → stt_match → none. `wake_constants` no longer exports `KNOWN_OWW_MODELS` / `match_known_oww_model` / `resolve_oww_model_path`. `_canonical_keyword(phrase)` returns the lower_snake slug of the phrase core only.
- Consumed by: Task 4 (provider), Task 6 (guard test asserts the exports are gone).

- [ ] **Step 1: Rewrite the brand-dependent tests to pin the NEW contract** (these currently assert `oww_keyword == "hey_jarvis"` etc.):

In `tests/unit/speech/test_wake_phrase.py` replace the pretrained-resolution tests (L239-295 region: `test_default_phrase_resolves_to_bundled_hey_jarvis_oww`, the "Rhasspy" companion, and the monkeypatched `resolve_oww_model_path` fixtures) with:

```python
def test_jarvis_phrase_resolves_generically_not_to_a_bundled_model():
    """Design 2026-07-07: no pretrained brand models — 'Hey Jarvis' is just a phrase."""
    cfg = _cfg(phrase="Hey Jarvis", engine="auto")
    plan = resolve_wake_plan(cfg, local_whisper_available=True, vosk_available=True)
    assert plan.engine == "vosk_kws"
    assert plan.oww_model_path is None


def test_brand_phrase_never_loads_an_upstream_package_model():
    """Typing a third-party brand word must NOT pull that brand's model."""
    cfg = _cfg(phrase="Alexa", engine="auto")
    plan = resolve_wake_plan(cfg, local_whisper_available=True, vosk_available=True)
    assert plan.engine == "vosk_kws"
    assert plan.oww_model_path is None
```

(Reuse the file's existing `_cfg` helper / config-stub pattern — read the top of the file first. Delete the now-meaningless monkeypatch fixtures for `resolve_oww_model_path` in this file, in `test_pipeline_wake_plan.py:50-58`, `test_wake_live_apply.py:69-77`, `test_wake_word_route.py:41-49` — replace each pretrained-scenario test with the vosk/stt_match expectation that same file already exercises elsewhere.)

In `test_wake_custom_model.py:156` the assertion `plan.oww_keyword == "hey_jarvis"` sits in a stale-custom-model scenario — the phrase there is the jarvis default; re-pin to the generic keyword slug (`assert plan.oww_keyword == "hey_jarvis"` becomes `assert plan.oww_keyword == "hey_jarvis"` ONLY if `_canonical_keyword("Hey Jarvis")` still slugs to `hey_jarvis` — it does (slug of the phrase), so this line may survive verbatim; verify against the new `_canonical_keyword`).

- [ ] **Step 2: Run those tests — expect FAIL** (new tests fail against old code)

Run: `.venv\Scripts\python.exe -m pytest tests/unit/speech/test_wake_phrase.py -v`
Expected: new generic tests FAIL (plan resolves to `openwakeword`).

- [ ] **Step 3: Implement in `wake_phrase.py`**

1. Remove `match_known_oww_model` and `resolve_oww_model_path` from the import at L33-41.
2. `_canonical_keyword` (L256-260): drop the `known = match_known_oww_model(phrase)` lookup; keep only the slug path (join `phrase_core(phrase)` with `_`).
3. Delete resolve step 2 entirely (L422-441, the `known`/`model_path` branch). `engine_pref == "openwakeword"` now simply falls through to vosk/stt (the existing `want_stt` condition at L482 already handles `engine_pref == "openwakeword"` — simplify its `and not known` clause to unconditional).
4. Rewrite the module docstring lines 13-20: the resolver chain is custom_onnx → vosk_kws → stt_match → hotkey-only degrade; no bundled model, no branded fallback.
5. Update the `verify_prefix` comment block L310-320: drop the "False only for a specific PRETRAINED model (alexa/mycroft/rhasspy)" sentence.

- [ ] **Step 4: Implement in `wake_constants.py`**

Delete `KNOWN_OWW_MODELS`, `_NON_WAKE_OWW_MODELS`, `match_known_oww_model`, `resolve_oww_model_path`, `_bundled_dir`, `_package_models_dir` (grep first: `_bundled_dir` must have no other consumer — `jarvis/assets/__init__.py` has its own copy). Remove the three `__all__` entries. Rewrite docstring lines 15-20 to describe the generic resolver only.

- [ ] **Step 5: Run the wake test modules — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/speech/test_wake_phrase.py tests/unit/speech/test_wake_custom_model.py tests/unit/speech/test_wake_plan_vosk.py tests/unit/speech/test_pipeline_wake_plan.py tests/unit/speech/test_wake_live_apply.py tests/unit/ui/test_wake_word_route.py tests/unit/speech/test_wake_dead_state.py -v`
Expected: PASS. Iterate on failures — the honest-degrade tests (`test_wake_phrase.py:307-374`) must stay green untouched.

- [ ] **Step 6: Commit**

```bash
git add jarvis/speech/wake_constants.py jarvis/speech/wake_phrase.py tests/unit/speech/test_wake_phrase.py tests/unit/speech/test_wake_custom_model.py tests/unit/speech/test_pipeline_wake_plan.py tests/unit/speech/test_wake_live_apply.py tests/unit/ui/test_wake_word_route.py
git commit -m "feat(wake): remove pretrained brand-model resolution — generic engine chain only"
```

---

### Task 4: Delete the bundled branded models + provider fallback

**Files:**
- Delete: `jarvis/assets/wakeword/hey_jarvis_v0.1.onnx`, `jarvis/assets/wakeword/hey_rhasspy_v0.1.onnx` (via `git rm`)
- Modify: `jarvis/assets/__init__.py:28-32` (`_WAKEWORD_FILES`), docstrings L4-7/L49
- Modify: `jarvis/plugins/wake/openwakeword_provider.py` (L144 `supported_keywords`, L148 ctor default, L164-168 model_path comment, L262-299 `_model_kwargs`, L304-306 docstring, module docstring L4, comments L138-140)
- Modify: `jarvis/speech/pipeline.py:914` (ctor default), `pipeline.py:1135-1138` (legacy else-branch), `pipeline.py:8218` (demo block), `jarvis/ui/desktop_app.py:2338`, `jarvis/speech/watchdog.py:133`
- Modify: `jarvis/speech/wake_verifier.py:3` (docstring `hey_jarvis_v0.1` mention)
- Test: `tests/unit/plugins/wake/test_openwakeword_bundled.py`

**Interfaces:**
- Consumes: Task 3 (no code resolves a bundled model anymore).
- Produces: `jarvis.assets.bundled_wakeword_models()` returns `{"melspec": Path, "embedding": Path}` or None. `OpenWakeWordProvider` without `model_path` degrades to the existing logged no-op (`_runtime_unavailable`-style) — it must NEVER pass bare keyword names to openWakeWord (that triggers the upstream brand-model auto-download).

- [ ] **Step 1: Update `test_openwakeword_bundled.py` to the new contract**

Replace the assertion at L20 (`kw["wakeword_models"][0].endswith("hey_rhasspy_v0.1.onnx")`) with tests pinning:

```python
def test_no_model_path_means_no_model_and_no_upstream_download():
    provider = OpenWakeWordProvider()  # no model_path
    assert provider._model_kwargs() is None  # sentinel: nothing to load


def test_custom_model_reuses_wordless_backbones(tmp_path):
    onnx = tmp_path / "my_word.onnx"
    onnx.write_bytes(b"stub")
    provider = OpenWakeWordProvider(model_path=str(onnx))
    kw = provider._model_kwargs()
    assert kw["wakeword_models"] == [str(onnx)]
    assert kw["melspec_model_path"].endswith("melspectrogram.onnx")
    assert kw["embedding_model_path"].endswith("embedding_model.onnx")
```

(Adapt to the file's existing import/monkeypatch style; keep its "bundle absent" sibling test but re-pin it to the new None-sentinel behavior instead of builtin names.)

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/plugins/wake/ -v`

- [ ] **Step 3: Implement**

1. `git rm jarvis/assets/wakeword/hey_jarvis_v0.1.onnx jarvis/assets/wakeword/hey_rhasspy_v0.1.onnx`
2. `jarvis/assets/__init__.py`: `_WAKEWORD_FILES = {"melspec": "melspectrogram.onnx", "embedding": "embedding_model.onnx"}`; update docstrings (bundle = word-agnostic backbones for user-trained models).
3. `openwakeword_provider.py::_model_kwargs`: keep the `self._model_path` branch (unchanged semantics); replace BOTH no-model_path branches (L289-299) with `return None` plus a comment: "No model configured -> nothing to arm. Never fall back to openWakeWord built-in keyword names: that auto-downloads third-party brand models (design 2026-07-07)." Make the caller (`_ensure_model` or equivalent — find where `_model_kwargs()` is consumed) treat `None` as: log one warning `"OpenWakeWord armed without a model — wake via this provider is disabled."`, set the existing `_runtime_unavailable = True` degrade flag, and return without constructing a model.
4. Neutralize keyword defaults: `supported_keywords = ()` (L144), ctor `keywords: tuple[str, ...] = ()` (L148), `pipeline.py:914` `wake_keywords: tuple[str, ...] = ()`, and the three call sites `desktop_app.py:2338`, `watchdog.py:133`, `pipeline.py:8218` → `wake_keywords=()`. The legacy else-branch `pipeline.py:1135-1138` stays structurally (it now builds a no-op provider; the plan-driven branch is the live path).
5. Sweep the touched files' comments/docstrings for `hey_jarvis`/`hey_rhasspy`/`alexa`/`mycroft` mentions and rewrite them generically (`wake_verifier.py:3`, provider module docstring L4, L138-140, L153, L166-168, L172, L304-306).

- [ ] **Step 4: Run the wake/plugin/speech test modules — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/plugins/wake/ tests/unit/speech/ -v -x`
Iterate until green.

- [ ] **Step 5: Commit**

```bash
git add -A jarvis/assets/wakeword jarvis/assets/__init__.py jarvis/plugins/wake/openwakeword_provider.py jarvis/speech/pipeline.py jarvis/ui/desktop_app.py jarvis/speech/watchdog.py jarvis/speech/wake_verifier.py tests/unit/plugins/wake/test_openwakeword_bundled.py
git commit -m "feat(wake): remove bundled branded wake models; provider without a model is an honest no-op"
```

(`git add -A <path>` scoped to `jarvis/assets/wakeword` records the deletions; every other path is explicit.)

---

### Task 5: Retire the jarvis-family special case (pattern + is_jarvis_default)

**Files:**
- Modify: `jarvis/speech/wake_constants.py:46-53` (delete `JARVIS_WAKE_PATTERN` + `__all__` L306), docstring L12-14
- Modify: `jarvis/speech/wake_phrase.py:33` (import), `:131/:139/:145-147` (`is_jarvis_default` field/property), `:244-245` (special-case in `compile_wake_matcher`), `:512` (`verify_prefix=matcher.is_jarvis_default` → `verify_prefix=False`)
- Modify: `jarvis/speech/rolling_whisper_wake.py:41-47` (DEFAULT_PATTERN re-export), `:185` (ctor default)
- Modify: `jarvis/speech/wake_verifier.py:24-28` (WAKE_PREFIX_PATTERN), `:107` (fallback), docstrings L1-14/L100-103/L120-121
- Test: `tests/unit/speech/test_wake_phrase.py:67-88`, `tests/unit/speech/test_wake_matcher_integration.py:25`, rolling-whisper tests constructing without a pattern

**Interfaces:**
- Consumes: Task 3/4 (no other consumer of the pattern remains).
- Produces: `compile_wake_matcher("Hey Jarvis")` returns the ordinary generic `WakeMatcher`; `RollingWhisperWake(pattern=...)` is REQUIRED (no default); `verify_wake_prefix(..., matcher=None)` fails OPEN (returns verified) with one warning log.

- [ ] **Step 1: Re-pin the BUG-009 guard on the generic matcher** — rewrite `test_wake_phrase.py:67-88` (family-match + is_jarvis_default tests) to:

```python
def test_hey_jarvis_phrase_matches_prefixed_and_rejects_bare_word():
    """BUG-009 invariant on the GENERIC matcher (jarvis special-case removed)."""
    m = compile_wake_matcher("Hey Jarvis")
    assert m.search("hey jarvis") is not None
    assert m.search("hallo jarvis") is not None   # prefix variants via WAKE_PREFIXES
    assert m.search("jarvis") is None              # bare word must NOT fire
    assert m.search("Thank you") is None
    assert m.search("Vielen Dank") is None  # i18n-allow: German STT token under test
```

Adjust the exact accepted-variant expectations to the real generic-matcher behavior discovered when running (the invariants that MUST hold: prefixed phrase matches; bare core word and unrelated speech do not). Delete `test_wake_matcher_integration.py:25` (`DEFAULT_PATTERN is JARVIS_WAKE_PATTERN`) and re-point that integration test at a generic compiled matcher.

- [ ] **Step 2: Run — expect FAIL/ERROR** (old special-case still active)

Run: `.venv\Scripts\python.exe -m pytest tests/unit/speech/test_wake_phrase.py tests/unit/speech/test_wake_matcher_integration.py -v`

- [ ] **Step 3: Implement**

1. `wake_phrase.py`: delete L244-245 special-case; remove the `is_jarvis_default` ctor param/backing/property; `:512` → `verify_prefix=False,` with comment "generic phrases carry no prefix-verify special case; custom_onnx plans keep verify_prefix=True".
2. `wake_constants.py`: delete `JARVIS_WAKE_PATTERN` (L46-53) + `__all__` entry + docstring L12-14.
3. `rolling_whisper_wake.py`: delete the L47 re-export; make `pattern` a required ctor argument (remove the `= DEFAULT_PATTERN` default at L185). Grep all constructors: `grep -rn "RollingWhisperWake(" jarvis/ tests/` — every call site must pass the plan matcher (production already does via the wake plan; fix any test that relied on the default by passing `compile_wake_matcher("hey test")`).
4. `wake_verifier.py`: delete L24 import + L28 `WAKE_PREFIX_PATTERN`; at L107, `matcher is None` → log warning `"Wake verify called without a matcher — failing open."` and return the verified/positive result (mirror the function's existing degrade-open return shape); rewrite the branded docstrings.

- [ ] **Step 4: Run the full speech suite — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/speech/ -v`
The AP-27 recall guard (`test_rolling_whisper_wake_silence_ghost.py`) MUST stay green — it exercises the rolling path with its own matcher.

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/wake_constants.py jarvis/speech/wake_phrase.py jarvis/speech/rolling_whisper_wake.py jarvis/speech/wake_verifier.py tests/unit/speech/test_wake_phrase.py tests/unit/speech/test_wake_matcher_integration.py
git commit -m "feat(wake): retire the jarvis-family special case — one generic matcher path"
```

(Add any further test files you had to touch in step 3.4 to the `git add` list explicitly.)

---

### Task 6: Guard test — no branded wake artifacts, ever again

**Files:**
- Test: `tests/unit/speech/test_no_branded_wake_artifacts.py` (create)

**Interfaces:** Consumes Tasks 3-5 (asserts their end state). This is the spec §6 "new guard test".

- [ ] **Step 1: Write the guard test**

```python
"""Guard: the shipped product contains no branded wake-word artifact.

Design spec 2026-07-07 (one-full-install-generic-wake): the wake system is
fully generic. Bundled assets may only be the word-agnostic openWakeWord
backbones; the resolver must not expose a brand map or an upstream-model
probe. If this test fails, a branded wake artifact is creeping back in.
"""
import re
from pathlib import Path

import jarvis.speech.wake_constants as wake_constants

_REPO = Path(__file__).resolve().parents[3]
_ASSETS = _REPO / "jarvis" / "assets" / "wakeword"
_WAKE_SOURCES = [
    *(_REPO / "jarvis" / "speech").glob("wake*.py"),
    *(_REPO / "jarvis" / "plugins" / "wake").glob("*.py"),
    _REPO / "jarvis" / "assets" / "__init__.py",
]
_BRANDS = re.compile(r"hey_jarvis|hey_rhasspy|alexa|mycroft", re.IGNORECASE)


def test_only_word_agnostic_backbones_are_bundled():
    onnx = sorted(p.name for p in _ASSETS.glob("*.onnx"))
    assert onnx == ["embedding_model.onnx", "melspectrogram.onnx"]


def test_brand_map_and_probe_are_gone():
    assert not hasattr(wake_constants, "KNOWN_OWW_MODELS")
    assert not hasattr(wake_constants, "match_known_oww_model")
    assert not hasattr(wake_constants, "resolve_oww_model_path")
    assert not hasattr(wake_constants, "JARVIS_WAKE_PATTERN")


def test_wake_sources_carry_no_brand_tokens():
    offenders = []
    for src in _WAKE_SOURCES:
        for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if _BRANDS.search(line):
                offenders.append(f"{src.name}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
```

- [ ] **Step 2: Run — expect PASS** (Tasks 3-5 done). Any failure = a missed cleanup; fix the source, not the test.

Run: `.venv\Scripts\python.exe -m pytest tests/unit/speech/test_no_branded_wake_artifacts.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/speech/test_no_branded_wake_artifacts.py
git commit -m "test(wake): guard against branded wake artifacts returning"
```

---

### Task 7: Stale-comment sweep in config + settings surface

**Files:**
- Modify: `jarvis/core/config.py:133-139` (wake comment block)
- Modify: `jarvis/ui/web/settings_routes.py` (~L448/465, only if it imports removed symbols — verify)

**Interfaces:** none downstream. `INSTANT_WAKE_PHRASES` (empty tuple) STAYS — `settings_routes.py:465` serves it and the frontend contract (`useWakeWord.ts:14`) plus `WakeWordPanel.test.tsx:83` pin "no chips rendered"; removing the API key buys nothing.

- [ ] **Step 1: Rewrite `config.py:133-139`** to:

```python
    # The human wake word the user wants — any phrase of their choice.
    # The single source of truth the UI/wizard edit.
    phrase: str = DEFAULT_WAKE_PHRASE
    # Detection engine. "auto" resolves the best generic path for the phrase:
    #   user-trained custom .onnx -> any-word Vosk keyword spotting ->
    #   local-Whisper transcript match -> honest hotkey-only degrade
    #   (no bundled model, no branded fallback — design 2026-07-07).
```

(Preserve the actual field lines exactly; only comment text changes.)

- [ ] **Step 2: Verify settings_routes still imports cleanly**

Run: `.venv\Scripts\python.exe -c "import jarvis.ui.web.settings_routes; import jarvis.core.config; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Run config tests + commit**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/core/test_wake_word_config.py tests/unit/core/test_trigger_require_hey_prefix.py -v`

```bash
git add jarvis/core/config.py
git commit -m "docs(config): describe the generic wake engine chain in the wake-word comments"
```

---

### Task 8: Documentation — README install story + wake user guide rewrite

**Files:**
- Modify: `README.md:94,107-125,149-170` (install section), `install/README.md:10-68,111`
- Rewrite: `docs/local-wakeword/USER-GUIDE-WAKE-WORD.md`
- Modify: `docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md` (top banner), `docs/telephony.md:38` (note extras now in full)
- Modify: `CLAUDE.md` §3 (one sentence) + run `python scripts/ci/sync_agents_md.py` (mirror rule)

**Interfaces:** consumes Tasks 1-2 (extras + installer semantics must be final before docs).

- [ ] **Step 1: README.md install section**
  - Flag table (121-125): replace the `--with-voice-local` row with a `--headless` description "minimal server install (advanced): API/WS only, torch-free base"; state that the default one-liner installs the FULL profile (desktop + telephony + channels + local voice; note approximate size honestly, "several GB with voice models").
  - Requirements line 94: drop the `--with-voice-local` reference.
  - Manual clone (158): keep `pip install -e .[full]` (now truly full).
- [ ] **Step 2: install/README.md** — same story: one official path = one-liner = full profile; `--headless` = advanced minimal; update flag examples 49-53; delete the stale "Future work" extras note at 111.
- [ ] **Step 3: Rewrite `docs/local-wakeword/USER-GUIDE-WAKE-WORD.md`** with this structure (all English, no brand names anywhere):

```markdown
# How to Set Your Wake Word

Personal Jarvis has no built-in wake word. You pick any phrase you like
during onboarding (or later in Settings); the recognizer is fully generic.

## Where to set it
1. Desktop app → Settings → Wake Word (or the first-run onboarding step)
2. Terminal wizard: `python -m jarvis --wizard` (SSH / headless setups)

## How recognition works (engine "auto")
1. Your own trained model (`custom_onnx`) if you supplied one for this phrase
2. Any-word keyword spotting (Vosk) — works offline on every machine, CPU only
3. Local-Whisper transcript match — higher accuracy; part of the full install
4. If none of these can serve the phrase, the wake word stays OFF and Jarvis
   says so — use the hotkey / push-to-talk instead. There is no hidden
   fallback word.

## No trademark words
Pick any word — just make sure it isn't someone else's trademark. The product
ships no pre-trained brand models and never downloads one.

## A restart is required
Wake-word changes apply after an app restart (Settings offers it).
```

Flesh each section out to match actual behavior (mention the per-language Vosk model download at setup, the fixed "Hey" prefix in the desktop onboarding step, and the push-to-talk alternative path).
- [ ] **Step 4: `CUSTOM-WAKE-WORD-DESIGN.md`** — add under the title: `> **Superseded in part (2026-07-07):** pretrained bundled models and the "degrade to a branded fallback" behavior described below were removed — see docs/superpowers/specs/2026-07-07-one-full-install-generic-wake-design.md. Kept for historical context.`
- [ ] **Step 5: CLAUDE.md §3** — inside the third bullet ("Work on EVERY OS..."), after the lockfile-guard sentence, add: `The ONE advertised install path is the [full] profile (incl. [local-voice]); the torch-free base install remains the internal floor for CI and tiny servers.` Then run `python scripts/ci/sync_agents_md.py` so `AGENTS.md` matches byte-for-byte.
- [ ] **Step 6: Docs privacy review** — dispatch the `docs-privacy-reviewer` agent over the changed docs files; it must report CLEAN before commit.
- [ ] **Step 7: Commit**

```bash
git add README.md install/README.md docs/local-wakeword/USER-GUIDE-WAKE-WORD.md docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md docs/telephony.md CLAUDE.md AGENTS.md
git commit -m "docs: one official full install; rewrite wake-word guide brand-free"
```

---

### Task 9: Full verification + iterate

**Files:** none new — fix whatever this uncovers.

- [ ] **Step 1: Lint/format touched Python**: `.venv\Scripts\python.exe -m ruff check jarvis/ tests/ install/` then `ruff format --check` on the touched files; fix findings.
- [ ] **Step 2: Fast suite**: `.venv\Scripts\python.exe -m pytest tests/ -m "not slow" -q` — iterate to green.
- [ ] **Step 3: Full suite**: `.venv\Scripts\python.exe -m pytest tests/ -q` — iterate to green (integration tests self-skip when prereqs missing; that is fine, but read WHY each skips).
- [ ] **Step 4: Guards**: run `python scripts/ci/check_requirements_sync.py`, `python scripts/ci/check_lockfile_universal.py`, `python scripts/ci/check_no_new_german.py` (if it supports local run), `python scripts/ci/sync_agents_md.py --check` — all must pass.
- [ ] **Step 5: Cold import smoke**: `.venv\Scripts\python.exe -c "import jarvis; import jarvis.speech.wake_phrase; import jarvis.speech.pipeline; print('ok')"`.
- [ ] **Step 6: Spec §6 non-maintainer trace** — write a short honest trace (in the final report, not a repo file): (a) fresh full install on a keyless box → wake via vosk works, whisper path present; (b) headless base floor → boots, wake degrades honestly; (c) no cross-family regression (wake has no cloud provider).
- [ ] **Step 7: Final commit if fixes were needed**, message `fix(wake|packaging): <what the verification uncovered>`.

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** D1.1→Task 4; D1.2→Task 3; D1.3→Task 3; D1.4→Task 5; D1.5→already-shipped (verified: onboarding placeholder "e.g. Nova", trademark notice) — no task needed; D1.6→Tasks 3-5 test updates; D1.7→Task 1; D2.1→Task 1; D2.2→Tasks 2+8; D2.3→Task 2 (`--headless`) + Task 8 docs; D2.4→Task 8 Step 5; §5 error handling→unchanged paths asserted in Tasks 3-5; §6 testing→Tasks 1-6+9.
- **Known deviation from spec:** spec §2.7 lists `core/config.py` among pvporcupine cleanups; the deprecated porcupine-era config keys are read-time back-compat pinned by tests and stay (CLAUDE.md §4 pattern). Recorded in Task 1 Interfaces.
- **Type consistency:** `bundled_wakeword_models()` returns melspec+embedding dict (Task 4) and the guard test (Task 6) asserts exactly those two files; `_model_kwargs()` None-sentinel consistent between Task 4 impl and test.
