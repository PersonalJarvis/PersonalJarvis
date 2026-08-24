import { useEffect } from "react";

import { ChatStage } from "@/components/home/ChatStage";
import { useAgentChatStore } from "@/store/agentChat";
import type { IdeWorkspace } from "@/store/ideChat";

/**
 * The Agentic IDE's chat surface — the agent chat, in this workspace's folder.
 *
 * It IS the front page's chat: same `ChatStage`, same composer, same store, so
 * a conversation started here can be picked up there and neither has to be
 * kept in step with the other by hand. What this component adds is the one
 * thing the IDE knows and the front page does not — WHICH FOLDER you are
 * working in. The workspace you opened is the working directory of every chat
 * started on this surface, which is what "logged into your workspace" means in
 * practice: the agent runs there, on those files, under that folder's
 * credentials.
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
  const activeSession = useAgentChatStore((s) => s.activeSession);
  const draftCwd = useAgentChatStore((s) => s.draft.cwd);
  const setDraft = useAgentChatStore((s) => s.setDraft);
  const newChat = useAgentChatStore((s) => s.newChat);

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
      <ChatStage />
    </div>
  );
}
