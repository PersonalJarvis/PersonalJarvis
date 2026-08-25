import { createContext, useContext, type ReactNode } from "react";

import { useAgentChatStore, type AgentChatStore, type AgentChatStoreHook } from "@/store/agentChat";

/**
 * Which agent-chat store a chat column reads — the seam between two chats.
 *
 * The front page's Chat section and the Agentic IDE's chat mode wear the same
 * face (`ChatStage`, the composer, the timeline) and are two different chats:
 * the front page is Jarvis with a keyboard (surface `jarvis`), the IDE runs
 * coding-agent sessions in a workspace's folder (surface `agent`). One store
 * per surface keeps their sessions, drafts and sockets apart — and this
 * context tells the shared components WHICH store they are standing on.
 *
 * Without a provider the components read the front page's store, so every
 * existing mount keeps working byte-identically; the IDE wraps its surface in
 * `AgentChatStoreProvider` with the agent store. Opening the IDE's last chat
 * must never put it on the front page again (maintainer, 2026-08-25).
 */
const AgentChatStoreContext = createContext<AgentChatStoreHook>(useAgentChatStore);

export function AgentChatStoreProvider({
  store,
  children,
}: {
  store: AgentChatStoreHook;
  children: ReactNode;
}) {
  return <AgentChatStoreContext.Provider value={store}>{children}</AgentChatStoreContext.Provider>;
}

/** The store hook of the chat this component is mounted in (front page by default). */
export function useAgentChatApi(): AgentChatStoreHook {
  return useContext(AgentChatStoreContext);
}

/** Select from the chat's own store — the drop-in for `useAgentChatStore(selector)`. */
export function useAgentChat<T>(selector: (state: AgentChatStore) => T): T {
  const store = useContext(AgentChatStoreContext);
  return store(selector);
}
