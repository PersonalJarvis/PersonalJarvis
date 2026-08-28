/**
 * Shared real level of the ASSISTANT's voice, for animation loops.
 *
 * The mirror of `voiceInputLevel`, with one difference that is the whole
 * point of the module: `null` and `0` mean different things here.
 *
 *   - a number (0 included) — a tap is running and this is what is currently
 *     leaving for the speaker. `0` is the honest "Jarvis is between words";
 *   - `null` — there is NO tap, so nothing may claim to draw the assistant's
 *     voice. Renderers fall back to a sweep: motion, promising nothing.
 *
 * ## Two sources, one owner
 *
 * Whoever plays the reply is who can measure it, and that is not always the
 * same side:
 *
 *   - `native` — the backend owns the speakers. `jarvis.audio.level_tap`
 *     measures each block and, importantly, schedules its level for the
 *     moment that block becomes AUDIBLE, so the number is in step with what
 *     the person hears rather than with what PortAudio accepted. It reaches
 *     the page on the `audio.level` frame.
 *   - `browser` — the browser realtime surface plays the reply itself, and
 *     the `pcm-playback` worklet measures the samples it writes.
 *
 * Both can be live at once (the backend keeps its player while a browser
 * session runs), so browser ownership wins the same way it does for the
 * microphone: while it is claimed, native writes are dropped instead of
 * fighting the browser's samples frame by frame.
 */
export type VoiceOutputLevelSource = "native" | "browser";

export const voiceOutputLevelRef: { current: number | null } = { current: null };

let browserOwnsOutput = false;

export function setVoiceOutputLevel(
  value: number,
  source: VoiceOutputLevelSource = "native",
): void {
  if (source === "native" && browserOwnsOutput) return;
  voiceOutputLevelRef.current = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/** Give up the tap — the surface that owned playback has stopped. */
export function clearVoiceOutputLevel(source: VoiceOutputLevelSource = "native"): void {
  if (source === "native" && browserOwnsOutput) return;
  voiceOutputLevelRef.current = null;
}

export function setBrowserVoiceOutputOwnership(active: boolean): void {
  browserOwnsOutput = active;
  voiceOutputLevelRef.current = null;
}
