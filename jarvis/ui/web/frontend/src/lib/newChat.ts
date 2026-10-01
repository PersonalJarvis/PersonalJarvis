/**
 * Starting a fresh typed chat — ONE definition for every door that opens one:
 * the sidebar's "New chat → Chat", and the desktop pet's pen control, which
 * arrives as the `ComposeRequested` bus event (`useWebSocket`).
 *
 * Store-level on purpose (no hooks), so a socket handler can call it as
 * easily as a click handler.
 */
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/** Drop the open text thread so the history's next message starts a new one. */
export function resetTextConversation(): void {
  const events = useEventStore.getState();
  events.setActiveConversation("text", null);
  events.seedThinkingTraces({});
  events.setMessages([]);
}

/** Land on an empty typed chat on the front page. */
export function startNewTextChat(): void {
  resetTextConversation();
  useAgentChatStore.getState().newChat();
  useHomeStore.getState().setSurface("chat");
  useEventStore.getState().setActiveSection("chats");
}

/** How many frames to wait for the composer to mount after a section switch. */
const FOCUS_ATTEMPTS = 30;

/**
 * Put the caret into the chat composer as soon as it is on screen.
 *
 * The composer mounts a few frames after `startNewTextChat` switches the
 * section (the chat view is code-split), so this retries once per animation
 * frame for about half a second and then gives up quietly: a window that
 * shows no composer has nothing to focus.
 */
export function focusChatComposer(): void {
  let attempts = 0;
  const tryFocus = () => {
    const field = document.querySelector<HTMLElement>("[data-jarvis-chat-input]");
    if (field) {
      field.focus();
      return;
    }
    attempts += 1;
    if (attempts < FOCUS_ATTEMPTS) window.requestAnimationFrame(tryFocus);
  };
  window.requestAnimationFrame(tryFocus);
}
