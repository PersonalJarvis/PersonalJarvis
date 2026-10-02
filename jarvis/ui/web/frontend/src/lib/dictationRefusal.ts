/**
 * Which `DictationRefused` reasons mean "the start did not happen".
 *
 * The composer sets `dictating` optimistically when the person presses the mic,
 * so a refused start must reset it or the recording pill sticks with a
 * waveform. Only the tokens below may do that: the others (`already_running`,
 * and the post-recording `nothing_to_paste`, `paste_unavailable`,
 * `history_disabled`) come from another trigger or from a recording that is
 * over, and must not drop a live pill. The composer's own start that hits an
 * already-running dictation arrives as an `ErrorOccurred` of layer
 * `ui.web.dictation`, which resets separately (see `useWebSocket`).
 *
 * A Python parity test (`tests/unit/ui/web/test_dictation_start_failures_parity.py`)
 * regex-reads the set below and checks every token is a refusal reason the
 * backend can send (`DICTATION_REFUSAL_REASONS`, AP-4): keep it a flat literal.
 */
export const DICTATION_START_FAILURES: ReadonlySet<string> = new Set([
  "microphone_unavailable",
  "no_stt",
  "handover_failed",
  "pipeline_not_running",
  "voice_session_active",
]);

export function isDictationStartFailure(reason: unknown): boolean {
  return typeof reason === "string" && DICTATION_START_FAILURES.has(reason);
}
