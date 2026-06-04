import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";

// Safety-Net: if the brain does not answer within 60s (no reply, no error
// event), we flip the indicator back. A backend hang must never leave the UI
// stuck in the wait-state.
const THINKING_TIMEOUT_MS = 60_000;

// Module-level timer (not component-scoped) so it survives across the input box
// and the empty-state prompt suggestions, which both dispatch through here.
let thinkingTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Dispatch a text chat message through the WebSocket — the single send path for
 * both the input box (ChatInput) and the clickable prompt suggestions in the
 * empty state (ChatsView). Centralising it keeps the wire shape
 * (`{ type: "message", kind: "text", content }`) in one place and avoids
 * multi-site drift.
 *
 * Mirrors the optimistic flow the chat relies on: echo a local `ui.user_message`
 * event, flip the optimistic thinking indicator on, and arm the 60s safety
 * timeout. Returns false when there is nothing to send or no live socket, so
 * callers can decide whether to clear their input.
 */
export async function sendChatMessage(content: string): Promise<boolean> {
  const text = content.trim();
  if (!text) return false;

  const client = getWSClient();
  if (!client) return false;

  // Route into the active conversation thread (mirrors ChatInput): lazily
  // create/resolve the thread so a one-click prompt lands in the same persisted
  // conversation the brain is seeded on. Fall back to the WS session thread.
  let threadId: string | undefined;
  try {
    threadId = await useEventStore.getState().ensureActiveThread();
  } catch {
    threadId = undefined;
  }

  client.send({
    type: "message",
    kind: "text",
    content: text,
    metadata: threadId ? { thread_id: threadId } : undefined,
  });

  const store = useEventStore.getState();
  store.pushEvent({
    id: `local-${Date.now()}`,
    name: "ui.user_message",
    layer: "ui",
    ts: Date.now(),
    payload: { content: text },
  });
  store.setChatThinking(true);

  if (thinkingTimer) clearTimeout(thinkingTimer);
  thinkingTimer = setTimeout(() => {
    useEventStore.getState().setChatThinking(false);
    thinkingTimer = null;
  }, THINKING_TIMEOUT_MS);

  return true;
}
