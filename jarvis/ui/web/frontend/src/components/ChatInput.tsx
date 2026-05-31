import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";
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
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  async function send() {
    const content = value.trim();
    if (!content) return;
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
    console.log("[ChatThinking] submit → true");
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      setChatThinking(false);
      console.log("[ChatThinking] timeout → false");
      timeoutRef.current = null;
    }, THINKING_TIMEOUT_MS);
    setValue("");
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
          className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs text-primary"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-1" aria-hidden>
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce" />
          </div>
          <span className="font-medium">Jarvis denkt nach…</span>
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
