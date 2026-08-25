import { useEffect } from "react";

import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { ChatStage } from "@/components/home/ChatStage";
import { useAgentSessionStore } from "@/store/agentChat";
import type { IdeWorkspace } from "@/store/ideChat";

/**
 * The Agentic IDE's chat surface — a coding-agent session in this workspace's folder.
 *
 * It wears the front page's face — same `ChatStage`, same composer — and is a
 * DIFFERENT chat: the front page is Jarvis with a keyboard, this is a coding
 * agent (Claude Code, Codex, the API loop) working in the folder. So it runs
 * on its own store (`useAgentSessionStore`, surface `agent`): its own
 * sessions, draft and socket, handed to the shared components through
 * `AgentChatStoreProvider`. Opening a chat here never changes what the front
 * page shows, and the other way round (maintainer, 2026-08-25). What this
 * component adds on top is the one thing the IDE knows and the front page
 * does not — WHICH FOLDER you are working in. The workspace you opened is the
 * working directory of every chat started on this surface, which is what
 * "logged into your workspace" means in practice: the agent runs there, on
 * those files, under that folder's credentials.
 *
 * A chat belonging to another folder is not silently re-pointed. Moving an
 * existing conversation to a new working directory mid-thread would change
 * what its own history refers to, so the surface opens a fresh chat instead
 * and leaves the old one in the sidebar where it was.
 *
 * This surface replaced the old "chat view", which read the terminal panes one
 * at a time behind a rail. That was a way of LOOKING at the grid; this is a
 * different thing to be doing (maintainer, 2026-08-24).
 */
export function IdeChatSurface({ workspace }: { workspace: IdeWorkspace }) {
  const folder = workspace.path;
  const activeSession = useAgentSessionStore((s) => s.activeSession);
  const draftCwd = useAgentSessionStore((s) => s.draft.cwd);
  const setDraft = useAgentSessionStore((s) => s.setDraft);
  const newChat = useAgentSessionStore((s) => s.newChat);

  useEffect(() => {
    if (!folder) return;
    // Order matters: dropping the open session FIRST means the cwd change is a
    // change to the draft for the next chat, not a PATCH that would move a
    // running conversation into a different folder.
    if (activeSession && activeSession.cwd !== folder) {
      newChat();
      void setDraft({ cwd: folder });
      return;
    }
    if (!activeSession && draftCwd !== folder) void setDraft({ cwd: folder });
  }, [folder, activeSession, draftCwd, newChat, setDraft]);

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      data-testid="ide-chat-surface"
      data-workspace={workspace.id}
      data-folder={folder}
    >
      <AgentChatStoreProvider store={useAgentSessionStore}>
        <ChatStage />
      </AgentChatStoreProvider>
    </div>
  );
}
