import { createStore, useStore } from "zustand";

import type { AgentChatStoreHook } from "@/store/agentChat";
import type { ToolChoice } from "./toolChoices";

export interface MessageDraft {
  text: string;
  choices: ToolChoice[];
}

const EMPTY_DRAFT: MessageDraft = { text: "", choices: [] };
const createDraftStore = () => createStore<{ drafts: Map<string | null, MessageDraft> }>(() => ({ drafts: new Map() }));
// Keep unsent text in memory, owned by its chat store, across view unmounts.
// Separate stores also isolate the front page from IDE panes and other agents.
const stores = new WeakMap<AgentChatStoreHook, ReturnType<typeof createDraftStore>>();

export function composerDraftsFor(chat: AgentChatStoreHook) {
  let store = stores.get(chat);
  if (!store) {
    store = createDraftStore();
    stores.set(chat, store);
  }
  return store;
}

export function readComposerDraft(chat: AgentChatStoreHook, sessionId: string | null): MessageDraft {
  return composerDraftsFor(chat).getState().drafts.get(sessionId) ?? EMPTY_DRAFT;
}

export function writeComposerDraft(chat: AgentChatStoreHook, sessionId: string | null, draft: MessageDraft) {
  const store = composerDraftsFor(chat);
  const previous = readComposerDraft(chat, sessionId);
  if (previous.text === draft.text && JSON.stringify(previous.choices) === JSON.stringify(draft.choices)) return;
  store.setState(({ drafts }) => {
    const next = new Map(drafts);
    if (draft.text || draft.choices.length) next.set(sessionId, draft);
    else next.delete(sessionId);
    return { drafts: next };
  });
}

export function useComposerDraft(chat: AgentChatStoreHook, sessionId: string | null): MessageDraft {
  return useStore(composerDraftsFor(chat), (state) => state.drafts.get(sessionId) ?? EMPTY_DRAFT);
}
