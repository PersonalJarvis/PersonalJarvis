import { useEffect, useRef, useState } from "react";

import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

/**
 * How far into the current answer the voice has got — the "words not yet
 * spoken are grey" effect for voice mode.
 *
 * No voice path reports a playback position per word, so this is an
 * estimate made where the sound is: while the assistant is speaking and the
 * playback level says audio is actually coming out, the cursor moves at a
 * steady speaking rate; in a pause between sentences it waits. A surface
 * that cannot observe its playback level (`voiceOutputLevelRef` is null)
 * counts the whole speaking state as audible. The cursor snaps to word ends
 * so a word never lights up half-way, and the moment the assistant stops
 * speaking the whole answer is shown as spoken — a guess is never left
 * hanging greyed out.
 *
 * Returns the number of leading characters of `text` to draw as spoken, or
 * null while nothing is being spoken (draw the text plainly).
 */
export function useSpokenCursor(text: string, speaking: boolean): number | null {
  // The count is kept with the text it was measured on, so a new answer
  // never borrows the last one's position for a frame.
  const [shown, setShown] = useState({ text: "", chars: 0 });
  const charsRef = useRef(0);
  const textRef = useRef("");
  const lastAudibleRef = useRef(0);

  // A new answer starts from the beginning; the same answer growing (more
  // snapshot words arriving) keeps its place.
  if (text !== textRef.current) {
    if (!text.startsWith(textRef.current) || !textRef.current) charsRef.current = 0;
    textRef.current = text;
  }

  useEffect(() => {
    if (!speaking || !text || typeof requestAnimationFrame === "undefined") return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const level = voiceOutputLevelRef.current;
      if (level === null || level > AUDIBLE_LEVEL) lastAudibleRef.current = now;
      // A word's own dips are not a pause: keep moving for a beat after
      // the last audible frame.
      if (now - lastAudibleRef.current < AUDIBLE_HOLD_MS) {
        charsRef.current = Math.min(textRef.current.length, charsRef.current + dt * CHARS_PER_SECOND);
      }
      const current = textRef.current;
      const next = wordEnd(current, charsRef.current);
      setShown((prev) =>
        prev.text === current && prev.chars === next ? prev : { text: current, chars: next },
      );
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [speaking, text]);

  if (!speaking || !text) return null;
  return shown.text === text ? shown.chars : wordEnd(text, charsRef.current);
}

/** A calm speaking voice: roughly 160 words a minute. */
export const CHARS_PER_SECOND = 15;
/** Playback level above which audio counts as audible. */
const AUDIBLE_LEVEL = 0.015;
const AUDIBLE_HOLD_MS = 300;

/**
 * The end of the word the cursor is in, so a word lights up whole. A cursor
 * at 0 stays at 0 — nothing has been said yet.
 */
export function wordEnd(text: string, chars: number): number {
  const at = Math.floor(chars);
  if (at <= 0) return 0;
  if (at >= text.length) return text.length;
  const space = text.slice(at).search(/\s/);
  return space === -1 ? text.length : at + space;
}
