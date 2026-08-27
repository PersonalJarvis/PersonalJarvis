/**
 * The setup assistant's own agent-chat store — surface `local-models`.
 *
 * A separate store instance means a separate socket, session list and draft:
 * the assistant's session never appears in the front page's chat list and
 * the front page's open chat never bleeds into the Local models section.
 */
import { createAgentChatStore } from "@/store/agentChat";

export const useLocalModelsAssistantStore = createAgentChatStore("local-models");
