import { useRef, useSyncExternalStore } from "react";

import { readSpeechPlayback, readTimedSpeechPlayback, readTimedSpeechSession, subscribeSpeechPlayback } from "@/lib/speechPlayback";

/**
 * Read positions confirmed by the player. A level is not a word position, and
 * neither elapsed wall time nor a transcript final proves audio was heard.
 * Unsupported players render ordinary text instead of a fabricated cursor.
 * Keep a bound line's position through pauses, cancellation and later replies.
 */
export function useSpokenCursor(text: string, eligible: boolean, lineId = ""): number | null {
  const playback = useSyncExternalStore(subscribeSpeechPlayback, readSpeechPlayback);
  const timed = useSyncExternalStore(subscribeSpeechPlayback, () => readTimedSpeechPlayback(lineId));
  const session = useSyncExternalStore(subscribeSpeechPlayback, readTimedSpeechSession);
  const bound = useRef<{ id: number; text: string; chars: number } | null>(null);
  if (bound.current && bound.current.text !== text) bound.current = null;
  if (timed) {
    // Caption snapshots can grow or be truncated before their timing frame
    // arrives. Preserve the confirmed common prefix instead of flashing grey.
    let chars = timed.text === text ? timed.chars : 0;
    if (timed.text !== text) {
      const limit = Math.min(timed.chars, text.length);
      while (chars < limit && timed.text[chars] === text[chars]) chars++;
      const last = text.charCodeAt(chars - 1);
      if (last >= 0xd800 && last <= 0xdbff) chars--; // Never split a surrogate pair.
    }
    bound.current = { id: -1, text, chars };
  }
  else if (!bound.current && eligible && session && lineId.startsWith(`live:${session}:`)) {
    // The bus caption and audio-control socket can arrive in either order.
    // A fresh caption stays grey until its playback metadata catches up.
    bound.current = { id: -1, text, chars: 0 };
  }
  if (playback?.text === text && (
    playback.id === bound.current?.id ||
    (eligible && playback.state !== "ended" && playback.state !== "cancelled")
  )) {
    bound.current = { id: playback.id, text, chars: playback.chars };
  }
  return timed?.text === text ? timed.chars : bound.current?.chars ?? null;
}
