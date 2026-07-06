// Mirror of jarvis/speech/wake_constants.py WAKE_ENGINES.
//
// This is the TypeScript layer of the five-layer anti-drift enum
// (Python <-> TOML <-> Pydantic <-> TypeScript <-> UI). It is kept in lockstep
// with the Python source of truth by
// tests/unit/speech/test_wake_engine_parity.py — if you add or rename an
// engine in wake_constants.py, update this array or the parity test fails.
//
// See docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md and
// docs/anti-drift-three-layer.md.

export const WAKE_ENGINES = [
  "auto",
  "openwakeword",
  "vosk_kws",
  "stt_match",
  "custom_onnx",
] as const;

export type WakeEngine = (typeof WAKE_ENGINES)[number];

// Human-friendly labels for the Settings dropdown (i18n keys live under
// settings_view.wake_word.engine_options.* in the locale files).
export const WAKE_ENGINE_I18N_KEY: Record<WakeEngine, string> = {
  auto: "settings_view.wake_word.engine_options.auto",
  openwakeword: "settings_view.wake_word.engine_options.openwakeword",
  vosk_kws: "settings_view.wake_word.engine_options.vosk_kws",
  stt_match: "settings_view.wake_word.engine_options.stt_match",
  custom_onnx: "settings_view.wake_word.engine_options.custom_onnx",
};
