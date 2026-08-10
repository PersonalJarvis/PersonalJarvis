import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Mic, Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// Safety net: if the brain doesn't respond within 60s (no reply, no error event),
// we revert the indicator. A backend hang must not leave the UI stuck in the
// wait state permanently.
const THINKING_TIMEOUT_MS = 60_000;

export function ChatInput() {
  const t = useT();
  const [value, setValue] = useState("");
  const connected = useEventStore((s) => s.connected);
  const wsWarming = useEventStore((s) => s.wsWarming);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const setChatThinking = useEventStore((s) => s.setChatThinking);
  // Most recent live reasoning step — the pill mirrors what the trace card
  // shows ("Using tool · wiki-recall") instead of a static "thinking…" label.
  // The selector returns a stable object ref while that step is unchanged.
  const activeStep = useEventStore((s) => {
    for (let i = s.thinkingSteps.length - 1; i >= 0; i--) {
      if (s.thinkingSteps[i].status === "active") return s.thinkingSteps[i];
    }
    return undefined;
  });
  // Mic-dictation: live transcript streams into the box as the user speaks.
  const dictating = useEventStore((s) => s.dictating);
  const dictationText = useEventStore((s) => s.dictationText);
  const dictationCommitSeq = useEventStore((s) => s.dictationCommitSeq);
  const setDictating = useEventStore((s) => s.setDictating);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The textarea content captured at dictation-start; interim transcripts are
  // rendered as `base + interim` so letters appear live without clobbering what
  // the user had already typed.
  const dictationBaseRef = useRef("");
  // True only between this button starting a dictation and that dictation's
  // final transcript arriving — the window in which `dictationBaseRef` is a
  // real snapshot rather than a leftover. It cannot be derived from the store's
  // `dictating` flag: the commit clears that flag in the SAME update that bumps
  // the sequence, so by the time the commit effect runs it already reads false.
  const mirroringRef = useRef(false);
  const lastCommitSeqRef = useRef(dictationCommitSeq);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  // While dictating, mirror the live interim tail into the textarea in real time.
  useEffect(() => {
    if (!dictating) return;
    const base = dictationBaseRef.current;
    const sep = base && dictationText ? " " : "";
    setValue(base + sep + dictationText);
  }, [dictating, dictationText]);

  // On a final dictation transcript, append it to the box exactly once (the seq
  // bump is the one-shot signal) and end the live-mirror.
  //
  // The base is only the snapshot taken at dictation START while the live
  // mirror is actually running, i.e. for a dictation this button began. A
  // dictation started by the keyboard shortcut never calls `startDictation`, so
  // the snapshot there is whatever the box held during the LAST button-driven
  // dictation — stale by minutes, and empty on a session that never used the
  // button at all. Using it appended the transcript onto a base that no longer
  // existed and so REPLACED whatever the user had typed. Outside the mirror the
  // live value is the only correct base, and the functional update reads it
  // without dragging `value` into the dependency array (which would re-run this
  // one-shot effect on every keystroke).
  useEffect(() => {
    if (dictationCommitSeq === lastCommitSeqRef.current) return;
    lastCommitSeqRef.current = dictationCommitSeq;
    const finalText = useEventStore.getState().dictationCommitText;
    setValue((current) => {
      const base = mirroringRef.current ? dictationBaseRef.current : current;
      const sep = base && finalText ? " " : "";
      return base + sep + finalText;
    });
    mirroringRef.current = false;
  }, [dictationCommitSeq]);

  async function send() {
    const content = value.trim();
    if (!content) return;
    // A pending dictation must not bleed into the next turn.
    if (dictating) stopDictation();
    const client = getWSClient();
    // Route the message into the active conversation so the brain (seeded on
    // resume) and the persisted thread line up. ensureActiveThread() lazily
    // creates a text thread for an unsaved "New chat" or a voice continuation.
    let threadId: string | undefined;
    try {
      threadId = await useEventStore.getState().ensureActiveThread();
    } catch {
      threadId = undefined; // fall back to the WS session thread
    }
    client?.send({
      type: "message",
      kind: "text",
      content,
      metadata: threadId ? { thread_id: threadId } : undefined,
    });
    useEventStore.getState().pushEvent({
      id: `local-${Date.now()}`,
      name: "ui.user_message",
      layer: "ui",
      ts: Date.now(),
      payload: { content },
    });
    setChatThinking(true);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      setChatThinking(false);
      timeoutRef.current = null;
    }, THINKING_TIMEOUT_MS);
    setValue("");
  }

  function startDictation() {
    // Capture the current text so the live transcript appends, not overwrites.
    dictationBaseRef.current = value;
    mirroringRef.current = true;
    setDictating(true);
    getWSClient()?.send({
      type: "command",
      action: "stt_dictate",
      payload: { mode: "start" },
    });
  }

  function stopDictation() {
    getWSClient()?.send({
      type: "command",
      action: "stt_dictate",
      payload: { mode: "stop" },
    });
    setDictating(false);
  }

  function toggleDictation() {
    if (dictating) stopDictation();
    else startDictation();
  }

  function onKeyDown(ev: KeyboardEvent<HTMLTextAreaElement>) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      send();
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {chatThinking && (
        <div
          className="flex min-w-0 items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs text-primary"
          role="status"
          aria-live="polite"
        >
          <span
            aria-hidden
            className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-primary/25 border-t-primary"
          />
          <span className="thinking-shimmer min-w-0 truncate font-medium">
            {activeStep
              ? `${t(activeStep.labelKey)}${activeStep.detail ? ` · ${activeStep.detail}` : ""}`
              : t("thinking.label")}
          </span>
        </div>
      )}
      {dictating && (
        <div
          className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs text-primary"
          role="status"
          aria-live="polite"
        >
          <span className="relative flex h-2 w-2" aria-hidden>
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/70" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
          </span>
          <span className="font-medium">{t("chats_view.dictation_listening")}</span>
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          // Marks the composer as the app's fallback dictation sink. The
          // delivery path needs to know whether it is on screen at all before
          // it hands a transcript to a component that may be unmounted — see
          // lib/dictationTarget.ts.
          data-jarvis-chat-input=""
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            connected
              ? t("chats_view.input_placeholder")
              : wsWarming
                ? t("voice_state.booting")
                : t("voice_state.offline")
          }
          disabled={!connected}
          rows={2}
          className="jarvis-input-surface flex-1 resize-none rounded-md border border-input px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
        />
        <Button
          type="button"
          data-jarvis-dictation-trigger
          onClick={toggleDictation}
          disabled={!connected}
          size="icon"
          variant={dictating ? "default" : "outline"}
          aria-label={dictating ? t("chats_view.dictation_stop") : t("chats_view.dictation_start")}
          title={dictating ? t("chats_view.dictation_stop") : t("chats_view.dictation_start")}
          className={cn(dictating && "animate-jarvis-pulse")}
        >
          {dictating ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </Button>
        <Button
          onClick={send}
          disabled={!connected || !value.trim()}
          size="icon"
          aria-label="Send"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
