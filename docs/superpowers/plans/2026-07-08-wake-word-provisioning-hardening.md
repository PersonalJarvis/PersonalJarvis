# Wake-Word Provisioning + Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a freely-chosen wake word work out-of-the-box on any fresh install by provisioning the `vosk_kws` model, shipping the openWakeWord backbones, warning loudly when only `stt_match` is reachable, verifying the mic/word at onboarding, and hardening the CPU `stt_match` path.

**Architecture:** A new pure download/extract helper fetches the per-language Vosk model into the exact directory `resolve_vosk_model_path` already probes. It is invoked from the setup prefetch (installer path) and an off-boot lazy task (runtime safety net), plus an in-app recovery route. `resolve_wake_plan` gains a loud-degrade case. The onboarding wake step gains a reusable live mic-dBFS check and a spoken-word confirmation. The ctranslate2 thread pool is bounded via environment before import.

**Tech Stack:** Python 3.11, `httpx` (repo HTTP standard), stdlib `zipfile`, `pytest` (asyncio_mode=auto, fakes in `tests/fakes/` — NOT unittest.mock), Vosk (base dep), React/TypeScript frontend (Vitest).

## Global Constraints

- **English-only artifacts** — every line committed is English (CLAUDE.md §1). Conversation may be German; code/comments/docs/tests/commits are English.
- **Never fatal / cross-platform** — every new path degrades gracefully offline, headless (`python:3.11-slim`, no audio/GPU/keyring), and without CUDA (CLAUDE.md §3, spec §4).
- **Boot budget (AP-26)** — no new work on the boot critical path; the Vosk fetch is setup-time or off-boot only. The boot-budget gate (`scripts/ci/check_boot_budget.py`, ≤8 s window, ≤20 s voice-usable/app-interactive) must stay green.
- **Base install stays torch-free/universal** — no new wheel; the Vosk model is *data*. `check_lockfile_universal.py` + `check_requirements_sync.py` stay green.
- **Data dir** — target `cfg.memory.data_dir` (env seam `JARVIS__MEMORY__DATA_DIR`, default `./data`), NEVER `paths.user_data_dir()`. Must byte-match `resolve_vosk_model_path` (`jarvis/speech/wake_constants.py:175-221`).
- **Model source** — `https://alphacephei.com/vosk/models/`, all Apache-2.0: `en`→`vosk-model-small-en-us-0.15.zip`, `de`→`vosk-model-small-de-0.15.zip`, `es`→`vosk-model-small-es-0.42.zip`. SHA-256 pinned, fail-closed on mismatch.
- **Commit discipline (CLAUDE.md §9)** — stage only your own files by explicit path; never `git add -A`/`git add .`; Conventional-Commit messages; never commit secrets. End commit messages with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Naming** — the agent system is "Jarvis-Agents"; no branded wake words added anywhere.

---

### Task 1: Ship the openWakeWord backbones (B2)

Whitelist and track the two word-agnostic feature models so `bundled_wakeword_models()` works in a clean checkout and `custom_onnx` can load. This is the fastest isolated win and unblocks user-trained models.

**Files:**
- Modify: `.gitignore:62-72` (add two whitelist lines next to the VAD whitelist)
- Track (force-add): `jarvis/assets/wakeword/melspectrogram.onnx`, `jarvis/assets/wakeword/embedding_model.onnx`
- Test: `tests/unit/assets/test_bundled_wakeword.py`

**Interfaces:**
- Consumes: `jarvis.assets.bundled_wakeword_models() -> dict[str, Path] | None` (`jarvis/assets/__init__.py:40`)
- Produces: nothing new (data only).

- [ ] **Step 1: Write the failing guard test**

```python
# tests/unit/assets/test_bundled_wakeword.py
"""The word-agnostic openWakeWord backbones must ship in a clean checkout, and
ONLY those two — no branded hey_* model may ever be smuggled back in."""
from pathlib import Path

import jarvis.assets as assets


def test_backbones_are_bundled_and_resolvable():
    models = assets.bundled_wakeword_models()
    assert models is not None, "backbones must be present in a clean checkout"
    assert models["melspec"].is_file()
    assert models["embedding"].is_file()


def test_wakeword_dir_holds_only_word_agnostic_models():
    d = Path(assets.__file__).resolve().parent / "wakeword"
    onnx = sorted(p.name for p in d.glob("*.onnx"))
    assert onnx == ["embedding_model.onnx", "melspectrogram.onnx"], (
        f"only word-agnostic backbones allowed, found: {onnx}"
    )
```

- [ ] **Step 2: Confirm the models exist locally, then verify the test fails only on tracking**

Run: `ls -la jarvis/assets/wakeword/` — confirm both `.onnx` files are present on disk.
Run: `git ls-files jarvis/assets/wakeword/` — expected: EMPTY (proves they are untracked → would not ship).
If the files are absent on disk, STOP and obtain them (they are the openWakeWord package's `melspectrogram.onnx` + `embedding_model.onnx`); do not fabricate.

- [ ] **Step 3: Whitelist the two files in `.gitignore`**

After the existing `!jarvis/assets/vad/silero_vad.onnx` line (`.gitignore:72`), add:

```gitignore
# The word-agnostic openWakeWord feature backbones are deliberately BUNDLED and
# shipped in-repo (jarvis/assets/__init__.py loads them for user-trained custom
# wake models). Scoped to the exact files so nearby *.onnx (user-trained custom
# wake models, Whisper cache) stay ignored. No branded hey_* model is tracked.
!jarvis/assets/wakeword/melspectrogram.onnx
!jarvis/assets/wakeword/embedding_model.onnx
```

- [ ] **Step 4: Force-add the tracked models and run the test**

Run: `git add .gitignore jarvis/assets/wakeword/melspectrogram.onnx jarvis/assets/wakeword/embedding_model.onnx tests/unit/assets/test_bundled_wakeword.py`
Run: `pytest tests/unit/assets/test_bundled_wakeword.py -v`
Expected: PASS (2 tests).
Run: `git ls-files jarvis/assets/wakeword/` — expected: both `.onnx` now listed.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(wake): ship word-agnostic openWakeWord backbones in-repo

bundled_wakeword_models() returned None in a clean checkout because *.onnx was
gitignored, so custom_onnx could never load. Whitelist + track the two
word-agnostic feature models (~2.4 MB); no branded model added.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Vosk model fetch helper (B1 core)

The pure download/extract module. No `jarvis.*` heavy imports beyond an optional config read; fully unit-testable with a fake HTTP client and a fake zip.

**Files:**
- Create: `jarvis/speech/wake_model_fetch.py`
- Test: `tests/unit/speech/test_wake_model_fetch.py`

**Interfaces:**
- Consumes: `resolve_vosk_model_path(language)` (`jarvis/speech/wake_constants.py:187`) to check presence; `_vosk_models_root()` layout (`wake_constants.py:175`).
- Produces:
  - `VOSK_MODELS: dict[str, VoskModelSpec]` — keys `"en"`, `"de"`, `"es"`.
  - `vosk_lang_for(language: str | None) -> str` — normalizes a config language to a supported model key (falls back to `"en"`).
  - `vosk_model_present(language: str | None, *, data_dir: str | None = None) -> bool`
  - `ensure_vosk_model(language: str | None, *, data_dir: str | None = None, http_get: Callable | None = None, echo: Callable[[str], None] = print) -> Path | None` — idempotent; returns the resolved model dir or `None` on non-fatal failure. NEVER raises for network/IO/hash errors.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/speech/test_wake_model_fetch.py
"""Vosk model fetch: idempotent, layout matches resolve_vosk_model_path,
hash-checked, never fatal offline."""
import io
import zipfile
from pathlib import Path

import pytest

from jarvis.speech import wake_model_fetch as wmf
from jarvis.speech.wake_constants import resolve_vosk_model_path


def _fake_model_zip() -> bytes:
    """A minimal zip that resolve_vosk_model_path will accept as a model:
    top folder vosk-model-small-de-0.15/ with an am/ dir + conf/model.conf."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("vosk-model-small-de-0.15/am/final.mdl", b"x")
        z.writestr("vosk-model-small-de-0.15/conf/model.conf", b"y")
    return buf.getvalue()


def test_lang_normalization():
    assert wmf.vosk_lang_for("de-DE") == "de"
    assert wmf.vosk_lang_for("de") == "de"
    assert wmf.vosk_lang_for("es") == "es"
    assert wmf.vosk_lang_for("auto") == "en"   # DEFAULT_LOCALE fallback
    assert wmf.vosk_lang_for(None) == "en"
    assert wmf.vosk_lang_for("fr") == "en"     # unsupported → default


def test_ensure_downloads_extracts_and_resolves(tmp_path, monkeypatch):
    data = _fake_model_zip()
    # Pin the fake zip's hash so the fetch's fail-closed check passes here.
    import hashlib
    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec(
            zip_name="vosk-model-small-de-0.15.zip",
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )

    def fake_get(url: str) -> bytes:
        assert url.endswith("vosk-model-small-de-0.15.zip")
        return data

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is not None
    # The layout must be exactly what the resolver accepts.
    resolved = resolve_vosk_model_path("de")  # honors JARVIS__MEMORY__DATA_DIR
    # (test sets the env below via monkeypatch to tmp_path)


def test_ensure_is_idempotent_noop_when_present(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_get(url: str) -> bytes:
        calls["n"] += 1
        return _fake_model_zip()

    import hashlib
    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec("vosk-model-small-de-0.15.zip",
                          hashlib.sha256(_fake_model_zip()).hexdigest()),
    )
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert calls["n"] == 1, "second call must be a no-op (already present)"


def test_hash_mismatch_rejects_and_is_nonfatal(tmp_path):
    def fake_get(url: str) -> bytes:
        return b"corrupt-not-a-zip"

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is None  # rejected, never installed, never raised


def test_offline_failure_is_nonfatal(tmp_path):
    def boom(url: str) -> bytes:
        raise OSError("network down")

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=boom)
    assert out is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/speech/test_wake_model_fetch.py -v`
Expected: FAIL (module `wake_model_fetch` does not exist).

- [ ] **Step 3: Implement the helper**

```python
# jarvis/speech/wake_model_fetch.py
"""Download + extract the per-language Vosk KWS model at setup / first run.

Fills the never-built fetch step the vosk_kws engine assumes: the model lands in
``<data>/wake_models/vosk/<lang>/`` exactly where ``resolve_vosk_model_path``
looks. Every failure is NON-FATAL (offline mirror, corrupt zip, hash mismatch):
the caller keeps going and the wake word degrades honestly. Apache-2.0 models
from alphacephei.com, SHA-256 pinned (fail-closed on mismatch).

Pure-ish: stdlib + httpx only; no heavy jarvis imports on module load.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.wake.model_fetch")

_BASE_URL = "https://alphacephei.com/vosk/models/"
# DEFAULT_LOCALE fallback (jarvis/core/turn_language.py) kept as a literal so this
# module needs no heavy import; supported app languages are en/de/es.
_DEFAULT_LANG = "en"


@dataclass(frozen=True)
class VoskModelSpec:
    zip_name: str
    sha256: str  # of the .zip; fill from Step 3b before shipping


# SHA-256 values are filled in Step 3b (download once, sha256sum, paste here).
VOSK_MODELS: dict[str, VoskModelSpec] = {
    "en": VoskModelSpec("vosk-model-small-en-us-0.15.zip", ""),
    "de": VoskModelSpec("vosk-model-small-de-0.15.zip", ""),
    "es": VoskModelSpec("vosk-model-small-es-0.42.zip", ""),
}


def vosk_lang_for(language: str | None) -> str:
    """Normalize a config language ('de-DE', 'auto', None) to a supported key."""
    lang = (language or "").strip().lower().split("-")[0]
    return lang if lang in VOSK_MODELS else _DEFAULT_LANG


def _models_root(data_dir: str | None) -> Path:
    base = data_dir or os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "wake_models" / "vosk"


def _lang_dir_has_model(lang_dir: Path) -> bool:
    if not lang_dir.is_dir():
        return False
    if (lang_dir / "am").is_dir() or (lang_dir / "conf" / "model.conf").is_file():
        return True
    return any(
        (sub / "am").is_dir() or (sub / "conf" / "model.conf").is_file()
        for sub in lang_dir.iterdir()
        if sub.is_dir()
    )


def vosk_model_present(language: str | None, *, data_dir: str | None = None) -> bool:
    lang = vosk_lang_for(language)
    return _lang_dir_has_model(_models_root(data_dir) / lang)


def _http_get(url: str) -> bytes:
    import httpx  # lazy: keep base import light

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def ensure_vosk_model(
    language: str | None,
    *,
    data_dir: str | None = None,
    http_get: Callable[[str], bytes] | None = None,
    echo: Callable[[str], None] = print,
) -> Path | None:
    """Idempotently ensure the Vosk model for ``language`` is on disk.

    Returns the language dir on success (or when already present), else ``None``.
    NEVER raises for network / IO / hash errors — the wake word degrades honestly.
    """
    lang = vosk_lang_for(language)
    lang_dir = _models_root(data_dir) / lang
    if _lang_dir_has_model(lang_dir):
        return lang_dir

    spec = VOSK_MODELS[lang]
    url = _BASE_URL + spec.zip_name
    getter = http_get or _http_get
    try:
        echo(f"downloading wake model '{spec.zip_name}' (one-time, ~40 MB)")
        blob = getter(url)
        digest = hashlib.sha256(blob).hexdigest()
        if spec.sha256 and digest != spec.sha256:
            echo(
                f"wake model '{spec.zip_name}' hash mismatch "
                f"(expected {spec.sha256[:12]}…, got {digest[:12]}…); skipping"
            )
            log.warning("Vosk model hash mismatch for %s — not installed.", lang)
            return None
        # Extract to a temp dir, then atomically move into place.
        lang_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(lang_dir.parent)) as td:
            tdir = Path(td)
            with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
                zf.extractall(tdir)
            if not any(_lang_dir_has_model(p) or _lang_dir_has_model(tdir)
                       for p in [tdir, *tdir.iterdir()]):
                echo(f"wake model '{spec.zip_name}' extracted but not a valid model; skipping")
                return None
            if lang_dir.exists():
                shutil.rmtree(lang_dir, ignore_errors=True)
            shutil.move(str(tdir), str(lang_dir))
        echo(f"wake model for '{lang}': ready")
        return lang_dir
    except Exception as exc:  # noqa: BLE001 — honest note, never fatal
        echo(
            f"wake model for '{lang}' could not be downloaded ({exc}); "
            "the wake word will use the fallback path until it succeeds"
        )
        log.warning("Vosk model fetch failed for %s: %s", lang, exc)
        return None


__all__ = [
    "VoskModelSpec",
    "VOSK_MODELS",
    "vosk_lang_for",
    "vosk_model_present",
    "ensure_vosk_model",
]
```

- [ ] **Step 3b: Record the real SHA-256 values**

Run (once, network required):
```bash
for f in vosk-model-small-en-us-0.15.zip vosk-model-small-de-0.15.zip vosk-model-small-es-0.42.zip; do
  curl -sL "https://alphacephei.com/vosk/models/$f" | sha256sum | sed "s#-#$f#"
done
```
Paste each digest into the matching `VoskModelSpec(...)` `sha256=` field. If network is unavailable in the build environment, leave `sha256=""` (the code treats empty as "unverified, accept") and file a follow-up to pin them — but prefer pinning.

- [ ] **Step 4: Make the layout test honor the temp data dir, run all tests**

In `test_ensure_downloads_extracts_and_resolves`, set the env so `resolve_vosk_model_path` reads tmp_path: add at the top of the test `monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))` and assert `resolved is not None and Path(resolved).name.startswith("vosk-model-small-de")`.
Run: `pytest tests/unit/speech/test_wake_model_fetch.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/wake_model_fetch.py tests/unit/speech/test_wake_model_fetch.py
git commit -m "feat(wake): add Vosk model fetch helper (never-fatal, hash-pinned)

Downloads + extracts the per-language Vosk KWS model into the exact dir
resolve_vosk_model_path probes. Idempotent, offline-safe, SHA-256 fail-closed.
This is the missing provisioning step the vosk_kws engine always assumed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the Vosk fetch into setup prefetch (B1 seam 1)

Installer path: `python -m jarvis --prefetch` now provisions the Vosk model too.

**Files:**
- Modify: `jarvis/setup/prefetch.py:65-100` (`prefetch_all`)
- Test: `tests/unit/setup/test_prefetch_vosk.py`

**Interfaces:**
- Consumes: `wake_model_fetch.ensure_vosk_model`, `wake_model_fetch.vosk_model_present`; `_load_config().stt.language`.
- Produces: nothing new; `prefetch_all` gains a Vosk step, still returns `int` (0 ok / 1 any-failure).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/setup/test_prefetch_vosk.py
from jarvis.setup import prefetch


def test_prefetch_calls_vosk_ensure(monkeypatch):
    seen = {}
    def fake_ensure(language, **kw):
        seen["lang"] = language
        return object()
    monkeypatch.setattr(prefetch, "_ensure_vosk", fake_ensure, raising=False)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    monkeypatch.setattr(prefetch, "_load_config",
                        lambda: type("C", (), {"stt": type("S", (), {"language": "de"})()})())
    lines = []
    rc = prefetch.prefetch_all(echo=lines.append)
    assert seen.get("lang") == "de"
    assert rc == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/setup/test_prefetch_vosk.py -v`
Expected: FAIL (`_ensure_vosk` not referenced / not wired).

- [ ] **Step 3: Wire it in**

In `jarvis/setup/prefetch.py`, add a seam + call. After the `_wakeword_bundle_present()` block and before the faster-whisper block in `prefetch_all`:

```python
def _ensure_vosk(language, **kw):
    """Seam for tests — heavy import stays out of module import."""
    from jarvis.speech.wake_model_fetch import ensure_vosk_model
    return ensure_vosk_model(language, **kw)


def _vosk_language() -> str | None:
    try:
        return _load_config().stt.language
    except Exception:  # noqa: BLE001 — config read must never brick prefetch
        return None
```

Inside `prefetch_all`, right after the wake-word-bundle note:

```python
    # Any-word wake model (vosk_kws): fetch the per-language model once so a
    # custom wake word resolves to the reliable engine instead of stt_match.
    lang = _vosk_language()
    try:
        out = _ensure_vosk(lang, echo=echo)
        if out is None:
            failed = True
    except Exception as exc:  # noqa: BLE001 — honest note, never fatal
        failed = True
        echo(f"wake model: could not provision ({exc}); it will retry at first run")
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/setup/test_prefetch_vosk.py tests/unit/setup/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/setup/prefetch.py tests/unit/setup/test_prefetch_vosk.py
git commit -m "feat(wake): provision the Vosk model in setup prefetch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Off-boot lazy net + in-app recovery route (B1 seams 2 & 3)

Runtime safety net (fetch once after the app is usable) and an in-app button.

**Files:**
- Modify: `jarvis/ui/desktop_app.py` (`_heavy_backend_bg`, ~1688) — schedule a one-shot fetch behind the live wake listener.
- Modify: `jarvis/ui/web/settings_routes.py` (add `POST /wake-word/download-model`)
- Test: `tests/unit/web/test_wake_download_route.py`

**Interfaces:**
- Consumes: `wake_model_fetch.ensure_vosk_model`, `vosk_model_present`; `_config(request).stt.language`.
- Produces: route returns `{"ok": bool, "present": bool, "message": str}`.

- [ ] **Step 1: Write the failing route test**

```python
# tests/unit/web/test_wake_download_route.py
"""POST /api/settings/wake-word/download-model triggers a non-fatal fetch."""
import pytest
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router as settings_router
from fastapi import FastAPI


@pytest.fixture
def client(monkeypatch):
    import jarvis.speech.wake_model_fetch as wmf
    monkeypatch.setattr(wmf, "ensure_vosk_model", lambda *a, **k: object())
    monkeypatch.setattr(wmf, "vosk_model_present", lambda *a, **k: True)
    app = FastAPI()
    app.include_router(settings_router)
    return TestClient(app)


def test_download_model_ok(client):
    r = client.post("/api/settings/wake-word/download-model")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["present"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/web/test_wake_download_route.py -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Add the route**

In `jarvis/ui/web/settings_routes.py`, after `set_wake_activation` (~line 659):

```python
@router.post("/wake-word/download-model")
async def download_wake_model(request: Request) -> dict[str, object]:
    """Provision (or repair) the per-language Vosk wake model in-app.

    Recoverable-in-app contract (CLAUDE.md §3): a user whose Vosk model is
    absent/dead gets a working reliable wake engine without editing jarvis.toml.
    Never 500s on a fetch failure — returns a clear message and the runtime lazy
    net remains the backstop.
    """
    import asyncio

    from jarvis.speech import wake_model_fetch as wmf

    cfg = _config(request)
    language = getattr(getattr(cfg, "stt", None), "language", None)
    out = await asyncio.to_thread(wmf.ensure_vosk_model, language)
    present = wmf.vosk_model_present(language)
    return {
        "ok": out is not None,
        "present": present,
        "message": (
            "Wake model ready." if present
            else "Could not download the wake model right now; it will retry "
                 "automatically. The wake word uses the fallback path until then."
        ),
    }
```

- [ ] **Step 4: Schedule the off-boot lazy fetch**

In `jarvis/ui/desktop_app.py` inside `_heavy_backend_bg` (after the `self._wake_model_loaded` gate, alongside the existing `loop.create_task(...)` fire-and-forget registrations ~1742-1746), add:

```python
        # One-shot: provision the Vosk wake model if missing, off the boot path
        # (AP-26). Never fatal — the wake word degrades honestly until it lands.
        async def _provision_wake_model() -> None:
            try:
                import asyncio as _a

                from jarvis.core.config import load_config
                from jarvis.speech import wake_model_fetch as _wmf

                lang = load_config().stt.language
                if not _wmf.vosk_model_present(lang):
                    await _a.to_thread(_wmf.ensure_vosk_model, lang)
            except Exception:  # noqa: BLE001 — a background probe never crashes boot
                log.debug("off-boot wake-model provision skipped", exc_info=True)

        loop.create_task(_provision_wake_model(), name="wake-model-provision")
```

Verify `log` is in scope in `desktop_app.py` (it is module-level); if the surrounding block uses a different logger name, match it.

- [ ] **Step 5: Run tests + boot-budget guard, then commit**

Run: `pytest tests/unit/web/test_wake_download_route.py -v`
Expected: PASS.
Run: `python scripts/ci/check_boot_budget.py` (if runnable locally) — expected: PASS (the fetch is off-boot).

```bash
git add jarvis/ui/web/settings_routes.py jarvis/ui/desktop_app.py tests/unit/web/test_wake_download_route.py
git commit -m "feat(wake): off-boot lazy Vosk provision + in-app download route

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Loud degrade when only stt_match is reachable (B3)

Stop `auto` from silently landing on `stt_match` for a custom word.

**Files:**
- Modify: `jarvis/speech/wake_phrase.py:445-483` (the stt_match branch of `resolve_wake_plan`)
- Test: `tests/unit/speech/test_wake_plan_loud_degrade.py`

**Interfaces:**
- Consumes: `resolve_wake_plan(cfg, *, local_whisper_available, language=None, vosk_available=None)`.
- Produces: for a custom phrase served only by `stt_match`, `plan.degraded is True` and `plan.message` names the remedy.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_wake_plan_loud_degrade.py
from types import SimpleNamespace

from jarvis.speech.wake_phrase import resolve_wake_plan


def _cfg(phrase, engine="auto"):
    return SimpleNamespace(phrase=phrase, engine=engine, custom_model_path="",
                           sensitivity=0.5, fuzzy_match_ratio=0.8)


def test_stt_match_custom_word_is_loudly_degraded():
    # No vosk model, whisper present → lands on stt_match. For a custom word
    # this must be a LOUD degrade, not silent success.
    plan = resolve_wake_plan(_cfg("Athena"), local_whisper_available=True,
                             vosk_available=False)
    assert plan.engine == "stt_match"
    assert plan.degraded is True
    assert "vosk" in plan.message.lower() or "reliable" in plan.message.lower()


def test_vosk_preferred_over_stt_match_when_available(monkeypatch):
    import jarvis.speech.wake_phrase as wp
    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda lang: "/fake/de")
    plan = resolve_wake_plan(_cfg("Athena"), local_whisper_available=True,
                             vosk_available=True, language="de")
    assert plan.engine == "vosk_kws"
    assert plan.degraded is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/speech/test_wake_plan_loud_degrade.py -v`
Expected: FAIL on the first test (`degraded` is currently False for a plain custom stt_match phrase).

- [ ] **Step 3: Implement the loud degrade**

In `jarvis/speech/wake_phrase.py`, in the stt_match branch (currently `wake_phrase.py:455-483`), replace the `degraded`/`message` computation so a plain custom phrase served only by stt_match is marked degraded with a remedy. Change the `else` message branch:

```python
        else:
            # A custom word served ONLY by the transcribe-and-match path is
            # UNRELIABLE for hard proper nouns (AP-27): the base model garbles
            # the name and matched stays 0. This is a LOUD degrade, not silent
            # success — point the user at the reliable any-word engine.
            degraded = True
            _lang = (language or "the configured language")
            message = (
                f"Custom phrase '{phrase}' is on the local-Whisper transcript "
                f"match — this is UNRELIABLE for a hard name. Download the Vosk "
                f"model for {_lang} to make it reliable (Settings → Wake word → "
                f"'Download wake model')."
            )
            log.warning(
                "Wake word '%s' resolved to stt_match only (no Vosk model, no "
                "custom ONNX) — recognition will be unreliable for a hard name. "
                "Provision the Vosk model for %s.", phrase, _lang,
            )
```

Note: the `custom_missing` / `custom_stale` branches above keep their existing
`degraded` values; only the plain `else` (ordinary custom phrase) flips to a loud
degrade. Verify the `degraded = custom_missing or engine_pref == "openwakeword"`
line above is not overwritten for those branches — restructure so the `else`
sets `degraded = True` explicitly without disturbing the earlier two.

- [ ] **Step 4: Run the tests + the existing wake-plan suite (no regressions)**

Run: `pytest tests/unit/speech/test_wake_plan_loud_degrade.py tests/unit/speech/ -v`
Expected: PASS, including the pre-existing `test_wake_plan_vosk.py` and the AP-27 silence-ghost guard.

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/wake_phrase.py tests/unit/speech/test_wake_plan_loud_degrade.py
git commit -m "fix(wake): loud degrade when a custom word lands on stt_match only

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: stt_match CPU thread-pool hardening (B5)

Bound the ctranslate2 / OpenMP pool via environment before ctranslate2 is imported. Defensive only — the real fix is Vosk bypassing this path.

**Files:**
- Modify: `jarvis/plugins/stt/fwhisper.py` (near the top-level import shield, ~line 35-71) OR the wake builder `jarvis/plugins/stt/__init__.py:676-694` — set env before the first ctranslate2 import.
- Test: `tests/unit/stt/test_ct2_thread_env.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a helper `_bound_ct2_threads()` that sets `OMP_NUM_THREADS`/`CT2_*` iff unset, called before ctranslate2 import.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/stt/test_ct2_thread_env.py
import os


def test_bound_ct2_threads_sets_env_when_unset(monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    from jarvis.plugins.stt.fwhisper import _bound_ct2_threads
    _bound_ct2_threads(default=2)
    assert os.environ["OMP_NUM_THREADS"] == "2"


def test_bound_ct2_threads_respects_user_override(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    from jarvis.plugins.stt.fwhisper import _bound_ct2_threads
    _bound_ct2_threads(default=2)
    assert os.environ["OMP_NUM_THREADS"] == "8"  # never clobber an explicit setting
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/stt/test_ct2_thread_env.py -v`
Expected: FAIL (`_bound_ct2_threads` missing).

- [ ] **Step 3: Implement + call before the ct2 import**

In `jarvis/plugins/stt/fwhisper.py`, add near the module top (before the inference-only import shield that pulls ctranslate2):

```python
def _bound_ct2_threads(default: int = 2) -> None:
    """Bound the ctranslate2/OpenMP CPU pool BEFORE ctranslate2 is imported.

    Defensive hardening for the CPU stt_match wedge (AP-24/AP-25/BUG-036): the
    ctranslate2 auto thread-pool can deadlock against other OpenMP consumers in
    the shared process. We cap it here and NEVER clobber an explicit user value.
    This does not claim to cure the deadlock (constellation-specific, AP-25) —
    the vosk_kws engine bypasses this path in the normal case.
    """
    import os

    for var in ("OMP_NUM_THREADS", "CT2_FORCE_CPU_THREADS"):
        os.environ.setdefault(var, str(default))
```

Call `_bound_ct2_threads()` at the point where the wake FasterWhisper model is built (`jarvis/plugins/stt/__init__.py` `build_wake_whisper`, before it constructs `FasterWhisperProvider`), so the env is set before ctranslate2's first load on the wake path.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/stt/test_ct2_thread_env.py tests/unit/stt/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/stt/fwhisper.py jarvis/plugins/stt/__init__.py tests/unit/stt/test_ct2_thread_env.py
git commit -m "fix(wake): bound ctranslate2 OpenMP pool to harden the CPU stt_match path

Defensive only (AP-25: constellation-specific deadlock); vosk_kws bypasses this
path in the normal case.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Onboarding mic + spoken-word verification (B4)

Make the wake step verify before it acknowledges.

**Files:**
- Modify: `jarvis/speech/diagnose.py:95-127` — extract a reusable pure measurement helper.
- Create: mic-level route in `jarvis/ui/web/settings_routes.py` (`GET /wake-word/mic-level`).
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.tsx` — mic meter + "say it once" before `acknowledgeWakeWord()` (`:53`).
- Test: `tests/unit/speech/test_mic_level_helper.py`, `tests/unit/web/test_mic_level_route.py`, frontend `WakeWordStep.test.tsx`.

**Interfaces:**
- Produces: `jarvis.speech.diagnose.measure_mic_dbfs(duration_s: float = 3.0) -> float` (pure; returns max dBFS, `-120.0` if no samples). `step_mic_level` calls it and keeps the CLI bars.
- Route `GET /api/settings/wake-word/mic-level` → `{"max_dbfs": float, "too_quiet": bool, "no_device": bool}`.

- [ ] **Step 1: Write the failing helper test**

```python
# tests/unit/speech/test_mic_level_helper.py
import pytest

from jarvis.speech import diagnose


@pytest.mark.asyncio
async def test_measure_mic_dbfs_no_device_returns_floor(monkeypatch):
    class _NoMic:
        async def __aenter__(self): raise OSError("no device")
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda: _NoMic())
    val = await diagnose.measure_mic_dbfs(duration_s=0.1)
    assert val == -120.0  # honest floor, never raises
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/speech/test_mic_level_helper.py -v`
Expected: FAIL (`measure_mic_dbfs` missing).

- [ ] **Step 3: Extract the pure helper**

In `jarvis/speech/diagnose.py`, add above `step_mic_level`:

```python
async def measure_mic_dbfs(duration_s: float = 3.0) -> float:
    """Return the max dBFS heard over ``duration_s``; -120.0 if no samples / no
    device. Pure measurement (no printing) — reused by the onboarding mic route
    and by step_mic_level's CLI bars. Never raises."""
    max_dbfs = -120.0
    try:
        async with MicrophoneCapture() as mic:
            t_end = time.time() + duration_s
            async for chunk in mic.stream():
                if time.time() >= t_end:
                    break
                arr = pcm_bytes_to_np(chunk.pcm)
                rms = float(np.sqrt(np.mean(arr * arr)) + 1e-12)
                max_dbfs = max(max_dbfs, 20.0 * float(np.log10(rms)))
    except Exception:  # noqa: BLE001 — headless / no device → honest floor
        return -120.0
    return max_dbfs
```

Then refactor `step_mic_level` to reuse it for the value while keeping its live bar rendering (the bar loop can stay; or it prints a summary from the returned value — keep the existing warn thresholds −40 / −3 dBFS).

- [ ] **Step 4: Add the mic-level route (test first)**

```python
# tests/unit/web/test_mic_level_route.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


@pytest.fixture
def client(monkeypatch):
    import jarvis.speech.diagnose as d
    async def fake_measure(duration_s=3.0): return -45.0
    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app = FastAPI(); app.include_router(router)
    return TestClient(app)


def test_mic_level_reports_too_quiet(client):
    r = client.get("/api/settings/wake-word/mic-level")
    assert r.status_code == 200
    b = r.json()
    assert b["max_dbfs"] == -45.0
    assert b["too_quiet"] is True
    assert b["no_device"] is False
```

Route in `settings_routes.py`:

```python
@router.get("/wake-word/mic-level")
async def wake_mic_level() -> dict[str, object]:
    """Live mic dBFS for the onboarding wake step. Never 500s; headless →
    no_device=True. Warn threshold −40 dBFS (matches jarvis.speech.diagnose)."""
    from jarvis.speech.diagnose import measure_mic_dbfs

    max_dbfs = await measure_mic_dbfs(duration_s=3.0)
    return {
        "max_dbfs": max_dbfs,
        "no_device": max_dbfs <= -119.9,
        "too_quiet": -119.9 < max_dbfs < -40.0,
    }
```

- [ ] **Step 5: Frontend — verify before acknowledge**

In `jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.tsx`, before the `await onb.acknowledgeWakeWord()` at `:53`:
- Add a "Test your microphone" control that calls `GET /api/settings/wake-word/mic-level` and shows a green/amber state (amber + i18n warning when `too_quiet`, neutral "no mic detected" when `no_device`).
- Add an optional "Say your wake word once" affordance (records ~3 s and shows heard-level feedback). Acknowledgment is NOT blocked (headless/no-mic contract), but a failed check is shown clearly.
- All UI strings go through i18n with an English source in `en.json` (+ `de.json`, `es.json` — product-surface localization is allowed there). Keep keys parallel across all three locale files.

Add/extend `WakeWordStep.test.tsx` (Vitest) to assert the mic-check renders and the amber warning shows when the mocked endpoint returns `too_quiet: true`.

- [ ] **Step 6: Run backend + frontend tests**

Run: `pytest tests/unit/speech/test_mic_level_helper.py tests/unit/web/test_mic_level_route.py -v`
Expected: PASS.
Run (frontend, in `jarvis/ui/web/frontend/`): `npm run test -- WakeWordStep`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add jarvis/speech/diagnose.py jarvis/ui/web/settings_routes.py \
  jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.tsx \
  jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.test.tsx \
  jarvis/ui/web/frontend/src/i18n/locales/en.json \
  jarvis/ui/web/frontend/src/i18n/locales/de.json \
  jarvis/ui/web/frontend/src/i18n/locales/es.json \
  tests/unit/speech/test_mic_level_helper.py tests/unit/web/test_mic_level_route.py
git commit -m "feat(onboarding): verify mic level + spoken wake word before acknowledging

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: End-to-end verification on the reproduction machine (spec §6)

Prove behavior, not just green tests. This machine is the exact bug case
(CUDA-less Windows, empty Vosk dir).

**Files:** none (verification only).

- [ ] **Step 1: Provision the model**

Run: `python -m jarvis --prefetch` (or POST the in-app route). Confirm the download completes.

- [ ] **Step 2: Confirm the resolver now finds it**

Run:
```bash
python -c "from jarvis.speech.wake_constants import resolve_vosk_model_path; print(resolve_vosk_model_path('de'))"
```
Expected: a non-None path under `data/wake_models/vosk/de/…`.

- [ ] **Step 3: Confirm the plan picks vosk_kws**

Run: `python -m jarvis.speech.diagnose` and inspect the wake plan line.
Expected: `engine=vosk_kws` (not `stt_match`).

- [ ] **Step 4: Confirm it fires**

Speak the configured phrase; confirm the log shows a wake (not `matched=0`).
Restart via `POST /api/settings/restart-app` (NOT Stop-Process) so the editable install takes effect first.

- [ ] **Step 5: Run the full affected suites + lint**

Run:
```bash
pytest tests/unit/speech/ tests/unit/assets/ tests/unit/setup/ tests/unit/web/ tests/unit/stt/ -v
ruff check jarvis/ && mypy jarvis/speech/wake_model_fetch.py
python scripts/ci/check_no_new_german.py   # if present — no new German added
```
Expected: all green.

- [ ] **Step 6: Sync AGENTS.md if CLAUDE.md changed** (only if you touched CLAUDE.md — this plan does not, but the mirror rule is binding: `python scripts/ci/sync_agents_md.py`).

---

## Self-Review

**Spec coverage:** B1 → Tasks 2,3,4,8. B2 → Task 1. B3 → Task 5. B4 → Task 7. B5 → Task 6. Error handling (spec §4) → the never-fatal contracts in Tasks 2-4,7. Testing (spec §5) → each task's tests + Task 8. Proof (spec §6) → Task 8. All covered.

**Placeholder scan:** the only deferred concrete value is the SHA-256 set (Task 2 Step 3b) — a real, scheduled step with a command, not a placeholder; empty-hash behavior is defined (accept-unverified) with a pin-preferred instruction.

**Type consistency:** `ensure_vosk_model` / `vosk_model_present` / `vosk_lang_for` / `VOSK_MODELS` / `VoskModelSpec` names are used identically across Tasks 2,3,4,8. `measure_mic_dbfs` identical across Task 7. Route paths `/wake-word/download-model`, `/wake-word/mic-level` consistent between backend and frontend/tests.

**Ordering rationale:** Task 1 (fast isolated win) → Tasks 2-4 (Vosk core) → Task 5 (small, isolated) → Task 6 (small, isolated, precedes the large frontend task) → Task 7 (large, frontend) → Task 8 (E2E proof).
