/**
 * Shared real microphone level for animation loops.
 *
 * This intentionally stays outside React state: native and browser capture
 * produce roughly 30 samples per second, while only canvas renderers need the
 * latest value. A mutable ref avoids repainting the application on each frame.
 */
export type VoiceInputLevelSource = "native" | "browser";

export const voiceInputLevelRef: { current: number } = { current: 0 };

const SAMPLE_TTL_MS = 220;
let updatedAt = Number.NEGATIVE_INFINITY;
let browserOwnsInput = false;

export function setVoiceInputLevel(
  value: number,
  source: VoiceInputLevelSource = "native",
  now = performance.now(),
): void {
  if (source === "native" && browserOwnsInput) return;
  voiceInputLevelRef.current = Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : 0;
  updatedAt = now;
}

export function clearVoiceInputLevel(source?: VoiceInputLevelSource): void {
  if (source === "native" && browserOwnsInput) return;
  voiceInputLevelRef.current = 0;
  updatedAt = Number.NEGATIVE_INFINITY;
}

export function setBrowserVoiceInputOwnership(active: boolean): void {
  browserOwnsInput = active;
  clearVoiceInputLevel();
}

export function readVoiceInputLevel(now = performance.now()): number {
  return now - updatedAt <= SAMPLE_TTL_MS ? voiceInputLevelRef.current : 0;
}
