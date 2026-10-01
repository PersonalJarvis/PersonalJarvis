import { useCallback, useEffect, useRef } from "react";

import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";

/**
 * Microphone dictation into a composer's text box — the final transcript is
 * appended once, when the dictation ends.
 *
 * Lifted from components/ChatInput.tsx so the agent composer dictates the
 * same way the classic one does: the same `stt_dictate` command, the same
 * store flags, the same one-shot commit on `dictationCommitSeq`.
 *
 * The interim transcript is deliberately NOT mirrored into the box while
 * speaking: the recording pill (green level meter + timer) is the only live
 * feedback, and the partial guesses flickering in the field read as noise.
 * The final text appends to whatever the box holds at commit time (the
 * functional update reads it without dragging `value` into a dependency
 * array), so text typed during the dictation is kept.
 *
 * The setter is read through a ref: callers pass an inline wrapper (the
 * society composer mirrors into its chip field), and with that wrapper in the
 * effect deps the commit effect would re-run on every render of the composer.
 */
export function useComposerDictation(
  setValue: (next: string | ((current: string) => string)) => void,
) {
  const dictating = useEventStore((s) => s.dictating);
  const dictationCommitSeq = useEventStore((s) => s.dictationCommitSeq);
  const setDictating = useEventStore((s) => s.setDictating);
  const lastCommitSeqRef = useRef(dictationCommitSeq);
  const setValueRef = useRef(setValue);
  setValueRef.current = setValue;

  useEffect(() => {
    if (dictationCommitSeq === lastCommitSeqRef.current) return;
    lastCommitSeqRef.current = dictationCommitSeq;
    const finalText = useEventStore.getState().dictationCommitText;
    if (!finalText) return;
    setValueRef.current((current) => {
      const sep = current && !/\s$/.test(current) ? " " : "";
      return current + sep + finalText;
    });
  }, [dictationCommitSeq]);

  const start = useCallback(() => {
    setDictating(true);
    getWSClient()?.send({ type: "command", action: "stt_dictate", payload: { mode: "start" } });
  }, [setDictating]);

  const stop = useCallback(() => {
    getWSClient()?.send({ type: "command", action: "stt_dictate", payload: { mode: "stop" } });
    setDictating(false);
  }, [setDictating]);

  const toggle = useCallback(() => {
    if (dictating) stop();
    else start();
  }, [dictating, start, stop]);

  return { dictating, start, stop, toggle };
}
