import { useRef, useSyncExternalStore } from "react";

import { readSpeechPlayback, subscribeSpeechPlayback } from "@/lib/speechPlayback";

/**
 * Read positions confirmed by the player. A level is not a word position, and
 * neither elapsed wall time nor a transcript final proves audio was heard.
 * Unsupported players render ordinary text instead of a fabricated cursor.
 * Keep a bound line's position through pauses, cancellation and later replies.
 */
export function useSpokenCursor(text: string, eligible: boolean): number | null {
  const playback = useSyncExternalStore(subscribeSpeechPlayback, readSpeechPlayback);
  const bound = useRef<{ id: number; text: string; chars: number } | null>(null);
  if (bound.current && bound.current.text !== text) bound.current = null;
  if (playback?.text === text && (
    playback.id === bound.current?.id ||
    (eligible && playback.state !== "ended" && playback.state !== "cancelled")
  )) {
    bound.current = { id: playback.id, text, chars: playback.chars };
  }
  return bound.current?.chars ?? null;
}
