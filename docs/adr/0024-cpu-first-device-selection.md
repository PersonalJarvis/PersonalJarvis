# ADR-0024 — CPU-First Compute-Device Selection

**Status:** Accepted · **Date:** 2026-07-08 · **Phase:** Cross-Platform / Cloud-First

## Context

Personal Jarvis is cloud-first (CLAUDE.md §3, `docs/SINGLE-SOURCE-OF-TRUTH.md`
§2): the baseline user is on a headless `python:3.11-slim` VPS with 1 vCPU, no
GPU, no CUDA, no audio hardware. The maintainer's RTX-5070-Ti workstation is a
power-user profile representing <0.1 % of the intended install base. A fresh
clone and a fresh deployment must therefore run on **CPU by default**, and a GPU
must be an explicit, validated opt-in — never an assumption.

In practice the CPU-first *policy* was already correct in most places
(`STTConfig.device = "cpu"`, `STTConfig.wake_device = "cpu"`,
`recommend_whisper` capability-gated in commit `8e17aeb2`), but it was
**scattered** across four modules, each re-deriving a slice of it, and it had two
gaps:

1. **No single selector.** Device resolution lived in `config.py` defaults,
   `hardware/detection.py::recommend_whisper`, `plugins/stt/__init__.py`, and
   `plugins/stt/fwhisper.py`. There was no one place expressing "how a requested
   device becomes a safe device", so the posture was easy to drift.
2. **A latent GPU-first default.** `FasterWhisperProvider.__init__` defaulted to
   `device="cuda"`. Every *bare* construction (`FasterWhisperProvider()` in
   `speech/pipeline.py`, `speech/diagnose.py`) therefore silently assumed the
   maintainer's card — the exact "works on my machine" defect §3 forbids, and an
   AP-25 hazard for the wake-adjacent VAD/backstop provider.

Two hard constraints shape any fix:

- **AP-25** — the always-on wake Whisper must never move to the GPU on bare CUDA
  *presence*; only a real out-of-process inference probe
  (`_wake_gpu_inference_verified`) may authorize it, because CUDA presence and
  CUDA usability diverge (a Blackwell sm_120 box had CUDA yet hung every
  CTranslate2 inference under one runtime constellation).
- **AP-26** — nothing may add a heavy import (`torch` / `ctranslate2`) or a
  blocking probe to a startup / hot path.

(The mission brief referenced a `DA-Agents.md` key doc that does not exist in
this tree; this ADR plus `docs/SINGLE-SOURCE-OF-TRUTH.md` §2 are the authoritative
device-posture documents instead.)

## Decision

### 1. One central policy: `jarvis/core/device.py::resolve_device`

A new pure module owns the **policy** — "which requested device becomes which
safe device" — and nothing else. It never imports `torch` / `ctranslate2`, so it
is dependency-light, instantly unit-testable, and safe on any path (AP-26).

```python
def resolve_device(
    requested: str | None,
    *,
    cuda_usable: bool | None = None,
    purpose: str = "",
) -> DeviceResolution: ...
```

Policy:

| Requested | `cuda_usable` | Result | Rationale |
|---|---|---|---|
| `"cpu"` | any | `cpu` | explicit CPU, never escalates |
| `"auto"` / `""` / `None` | `True` | `cuda` | auto may opt UP only when GPU is proven |
| `"auto"` / `""` / `None` | `False` / `None` | `cpu` | cloud-first floor |
| `"cuda"` / `"cuda:0"` / `"gpu"` | `True` | `cuda` / `cuda:0` | verified opt-in honored |
| `"cuda"` / `"gpu"` | `False` | `cpu` (`fell_back=True`) | **validated fallback + WARNING** |
| `"cuda"` / `"gpu"` | `None` | `cuda` | explicit config IS the opt-in; backend self-heals |
| anything else | any | `cpu` | fail-closed to the safe device |

Every GPU decision is **logged** (INFO on adopt, WARNING on validated fallback),
and the returned `DeviceResolution(device, requested, fell_back, reason)` carries
a human-readable English reason for surfacing or auditing.

### 2. Capability is INJECTED, never derived inside the policy

`resolve_device` takes `cuda_usable` as a parameter instead of probing. This is
the load-bearing design choice: the correct capability probe differs per consumer.

- The **wake** path keeps its strict, unchanged out-of-process inference gate
  (`build_wake_whisper` + `_wake_gpu_inference_verified`, AP-25). It is the
  canonical *strictest* instance of this policy and is **not refactored** by this
  ADR — touching it risks the BUG-036/037 hallucinating-ASR and hung-inference
  classes.
- The **utterance** STT factory (`_build_local_fallback`) routes its config
  device through `resolve_device(..., cuda_usable=None, purpose="stt-utterance")`.
  With `None`, an explicit `device = "cuda"` in `jarvis.toml` is honored (the
  opt-in), everything else lands on CPU, and the decision is logged. The
  construction-time self-heal in `fwhisper.py` (`WhisperModel` build failure →
  retry on `cpu`/`int8`) remains the runtime safety net.

### 3. Flip the latent GPU-first default

`FasterWhisperProvider.__init__` now defaults to `device="cpu"`. A bare
construction is CPU-first; real callers still pass the config-resolved device
explicitly. No test asserted a `cuda` default on a bare provider, and the
wake-builder tests pass device explicitly, so behavior for configured paths is
unchanged.

## Consequences

**Positive:**

- One documented, unit-tested source of truth for the CPU-first posture; drift is
  now visible as a diff against `resolve_device` rather than spread across four
  modules.
- GPU usage is explicit and logged everywhere it is adopted, and a known-bad GPU
  degrades to CPU with a clear WARNING instead of a silent construction failure.
- Bare-construction and `auto` paths can no longer silently assume the
  maintainer's card — the headless-VPS baseline holds by default.
- The wake path's strict AP-25 gate is preserved untouched; the policy module is
  strictly additive there.

**Negative / trade-offs:**

- `device = "auto"` now resolves CPU-first for the utterance provider instead of
  delegating to CTranslate2's own auto-pick (which could choose CUDA). This is a
  deliberate, mission-aligned behavior flip: silent `auto → GPU` is exactly the
  friction being removed. A power user opts in with an explicit `device = "cuda"`.
- Consumers that want GPU on a bare construction must now pass `device` (or a
  verified `cuda_usable`) explicitly. This is the intended explicit-opt-in cost.

## Alternatives Considered

**Alt-A — Let `resolve_device` probe CUDA itself (rejected).** Simpler call
sites, but it would (1) import `torch`/`ctranslate2` into a core module and risk
sitting on a hot path (AP-26), and (2) force one probe strategy on every
consumer, breaking the wake path's strict out-of-process inference gate (AP-25).
Injecting the verdict keeps the module pure and each consumer correct.

**Alt-B — Refactor the wake path through the same helper (rejected).** The wake
resolution in `build_wake_whisper` is finely tuned against AP-25/AP-27 and the
BUG-036/037 classes. Re-routing it for cosmetic unification would risk a
high-severity voice regression for no functional gain. The ADR documents it as
the canonical strict instance instead.

**Alt-C — Leave defaults as-is and only document them (rejected).** The latent
`device="cuda"` default and the scattered logic are precisely the operational
friction the migration targets. Documentation without the central selector and
the default flip would not change what a fresh clone actually does.

## Cross-References

- Policy: `jarvis/core/device.py` (new)
- Consumers: `jarvis/plugins/stt/__init__.py` (`_build_local_fallback`),
  `jarvis/plugins/stt/fwhisper.py` (default flip)
- Wake gate (canonical strict instance, unchanged): `jarvis/plugins/stt/__init__.py`
  (`build_wake_whisper`, `_wake_gpu_inference_verified`)
- Recommendation gate: `jarvis/hardware/detection.py::recommend_whisper`
- Tests: `tests/unit/core/test_device.py`
- Anti-patterns: AP-21 (gate on capability, not provider), AP-24/AP-25 (native
  inference + GPU wake), AP-26 (no heavy init on the critical path)
- Doctrine: CLAUDE.md §3, `docs/SINGLE-SOURCE-OF-TRUTH.md` §2, `CLOUD.md`
