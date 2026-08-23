import { useCallback, useEffect, useRef } from "react";

import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";

/**
 * Microphone dictation into a composer's text box — the live interim
 * transcript mirrored while speaking, the final transcript appended once.
 *
 * Lifted from components/ChatInput.tsx so the agent composer dictates the
 * same way the classic one does: the same `stt_dictate` command, the same
 * store flags, the same one-shot commit on `dictationCommitSeq`.
 *
 * The base is the snapshot taken at dictation START only while the live
 * mirror is running — i.e. for a dictation this composer began. A dictation
 * started by the keyboard shortcut never calls `start`, so its final text
 * appends to whatever the box holds at that moment (the functional update
 * reads it without dragging `value` into a dependency array).
 */
export function useComposerDictation(
  value: string,
  setValue: (next: string | ((current: string) => string)) => void,
) {
  const dictating = useEventStore((s) => s.dictating);
  const dictationText = useEventStore((s) => s.dictationText);
  const dictationCommitSeq = useEventStore((s) => s.dictationCommitSeq);
  const setDictating = useEventStore((s) => s.setDictating);
  const baseRef = useRef("");
  const mirroringRef = useRef(false);
  const lastCommitSeqRef = useRef(dictationCommitSeq);

  useEffect(() => {
    if (!dictating) return;
    const base = baseRef.current;
    const sep = base && dictationText ? " " : "";
    setValue(base + sep + dictationText);
  }, [dictating, dictationText, setValue]);

  useEffect(() => {
    if (dictationCommitSeq === lastCommitSeqRef.current) return;
    lastCommitSeqRef.current = dictationCommitSeq;
    const finalText = useEventStore.getState().dictationCommitText;
    setValue((current) => {
      const base = mirroringRef.current ? baseRef.current : current;
      const sep = base && finalText ? " " : "";
      return base + sep + finalText;
    });
    mirroringRef.current = false;
  }, [dictationCommitSeq, setValue]);

  const start = useCallback(() => {
    baseRef.current = value;
    mirroringRef.current = true;
    setDictating(true);
    getWSClient()?.send({ type: "command", action: "stt_dictate", payload: { mode: "start" } });
  }, [value, setDictating]);

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
