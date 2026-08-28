/**
 * Shared real level of the ASSISTANT's voice, for animation loops.
 *
 * The mirror of `voiceInputLevel`, with one difference that is the whole
 * point of the module: `null` and `0` mean different things here.
 *
 *   - a number (0 included) — a tap is running and this is what is currently
 *     leaving for the speaker. `0` is the honest "Jarvis is between words";
 *   - `null` — there is NO tap on this device, so nothing may claim to draw
 *     the assistant's voice.
 *
 * That distinction exists because only the browser realtime surface plays
 * the reply itself (components/voice/BrowserRealtimeControl → the
 * `pcm-playback` worklet, which measures the samples it writes). When the
 * backend speaks through the OS audio device instead, the page never sees a
 * sample, and a waveform drawn there would be an invention. Renderers fall
 * back to a sweep on `null`: motion, promising nothing.
 */
export const voiceOutputLevelRef: { current: number | null } = { current: null };

export function setVoiceOutputLevel(value: number): void {
  voiceOutputLevelRef.current = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/** Give up the tap — the surface that owned playback has stopped. */
export function clearVoiceOutputLevel(): void {
  voiceOutputLevelRef.current = null;
}
