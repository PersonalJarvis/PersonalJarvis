import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Mic, Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// Safety-Net: wenn das Brain in 60s nicht antwortet (kein Reply, kein Fehler-Event),
// drehen wir den Indikator zurueck. Backend-Hangs duerfen die UI nicht permanent
// in den Wait-State versetzen.
const THINKING_TIMEOUT_MS = 60_000;

export function ChatInput() {
  const t = useT();
  const [value, setValue] = useState("");
  const connected = useEventStore((s) => s.connected);
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
  useEffect(() => {
    if (dictationCommitSeq === lastCommitSeqRef.current) return;
    lastCommitSeqRef.current = dictationCommitSeq;
    const finalText = useEventStore.getState().dictationCommitText;
    const base = dictationBaseRef.current;
    const sep = base && finalText ? " " : "";
    setValue(base + sep + finalText);
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
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={connected ? t("chats_view.input_placeholder") : t("voice_state.offline")}
          disabled={!connected}
          rows={2}
          className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
        />
        <Button
          type="button"
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
