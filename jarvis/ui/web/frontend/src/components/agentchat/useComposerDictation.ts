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
 *
 * `stopAndSend` lets Send end a running dictation: the mic stops, and the
 * composer's `onSend` runs once the final transcript has landed in the box
 * (or after FINAL_WAIT_MS, so a transcript that never comes cannot strand
 * the message). `onSend` is also read through a ref, so the call made after
 * the transcript lands sees the box as it is then, not as it was at click.
 */
const FINAL_WAIT_MS = 8000;

export function useComposerDictation(
  setValue: (next: string | ((current: string) => string)) => void,
  onSend?: () => void,
) {
  const dictating = useEventStore((s) => s.dictating);
  const dictationCommitSeq = useEventStore((s) => s.dictationCommitSeq);
  const dictationFinalSeq = useEventStore((s) => s.dictationFinalSeq);
  const setDictating = useEventStore((s) => s.setDictating);
  const lastCommitSeqRef = useRef(dictationCommitSeq);
  const setValueRef = useRef(setValue);
  setValueRef.current = setValue;
  const onSendRef = useRef(onSend);
  onSendRef.current = onSend;
  const sendTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFinalSeqRef = useRef(dictationFinalSeq);

  const flushSend = useCallback(() => {
    if (sendTimerRef.current === null) return;
    clearTimeout(sendTimerRef.current);
    sendTimerRef.current = null;
    // A tick later, so the box has re-rendered with the transcript first.
    setTimeout(() => onSendRef.current?.(), 0);
  }, []);

  useEffect(() => {
    if (dictationFinalSeq === lastFinalSeqRef.current) return;
    lastFinalSeqRef.current = dictationFinalSeq;
    flushSend();
  }, [dictationFinalSeq, flushSend]);

  useEffect(
    () => () => {
      if (sendTimerRef.current !== null) clearTimeout(sendTimerRef.current);
    },
    [],
  );

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

  const stopAndSend = useCallback(() => {
    if (sendTimerRef.current !== null) return;
    sendTimerRef.current = setTimeout(flushSend, FINAL_WAIT_MS);
    stop();
  }, [flushSend, stop]);

  return { dictating, start, stop, toggle, stopAndSend };
}
