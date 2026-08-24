import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, Folder, MessageSquare, Plus, Trash2 } from "lucide-react";

import { CONVERSATIONS_REFRESH_MS } from "@/hooks/useConversations";
import { useAgentChatStore } from "@/store/agentChat";
import { useIdeChatStore } from "@/store/ideChat";
import { folderLeaf } from "@/lib/folderPath";
import { folderColor } from "@/components/agentic/folderColor";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** Chats shown per folder before "Show more" — enough to recognise the folder. */
const FOLDED = 5;

/**
 * The sidebar, wearing the Agentic IDE's chat face.
 *
 * While the IDE is in chat mode the app's own navigation steps aside and this
 * column takes over: the conversations you are having, grouped by the FOLDER
 * they are running in. That grouping is the whole point — with several
 * workspaces open, a flat list of eleven chats says nothing about which
 * repository any of them is touching. The folder you have open leads, under
 * its own band; everything else follows underneath.
 *
 * The way back is the first thing in the column, not a hidden gesture: a
 * sidebar that swallows the navigation with no visible exit is a trap, so the
 * "Sections" button sits at the top where a back button belongs.
 */
export function WorkspaceChats() {
  const t = useT();
  const workspace = useIdeChatStore((s) => s.workspace);
  const setSidebarFace = useIdeChatStore((s) => s.setSidebarFace);

  const sessions = useAgentChatStore((s) => s.sessions);
  const activeSessionId = useAgentChatStore((s) => s.activeSessionId);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const openSession = useAgentChatStore((s) => s.openSession);
  const removeSession = useAgentChatStore((s) => s.removeSession);
  const newChat = useAgentChatStore((s) => s.newChat);
  const setDraft = useAgentChatStore((s) => s.setDraft);

  useEffect(() => {
    void loadSessions();
    const id = window.setInterval(() => void loadSessions(), CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadSessions]);

  const groups = useMemo(
    () => groupByFolder(sessions, workspace?.path ?? ""),
    [sessions, workspace?.path],
  );
  // The open workspace's folder always sorts first (see `groupByFolder`), so
  // the leading group is "this workspace" — unless nothing is open, in which
  // case every group is just another folder.
  const openFolder = workspace?.path ?? "";
  const leads = openFolder !== "" && groups[0]?.folder === openFolder;
  const here = leads ? groups[0] : null;
  const elsewhere = leads ? groups.slice(1) : groups;

  /** A fresh chat in `folder` — the folder is the pick, the chat is empty. */
  const startChat = (folder: string) => {
    newChat();
    void setDraft({ cwd: folder });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="workspace-chats">
      <div className="shrink-0 space-y-1 px-2 pb-2 pt-2">
        <button
          type="button"
          data-testid="workspace-chats-back"
          onClick={() => setSidebarFace("sections")}
          className="flex w-full items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <ChevronLeft aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
          {t("ide_chats.back_to_sections")}
        </button>
        <button
          type="button"
          data-testid="workspace-chats-new"
          onClick={() => startChat(workspace?.path ?? "")}
          className="flex w-full items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <span className="flex h-4 w-4 items-center justify-center rounded bg-primary text-primary-foreground">
            <Plus aria-hidden className="h-3 w-3" />
          </span>
          {t("sidebar.new_chat")}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-1 pb-3">
        {here && (
          <>
            <Band>{t("ide_chats.this_workspace")}</Band>
            <FolderGroup
              group={here}
              activeSessionId={activeSessionId}
              onOpen={openSession}
              onDelete={(id) => void removeSession(id)}
              onNewChat={() => startChat(here.folder)}
            />
          </>
        )}
        {elsewhere.length > 0 && (
          <>
            <Band>{t("ide_chats.other_folders")}</Band>
            {elsewhere.map((group) => (
              <FolderGroup
                key={group.folder || "~"}
                group={group}
                activeSessionId={activeSessionId}
                onOpen={openSession}
                onDelete={(id) => void removeSession(id)}
                onNewChat={() => startChat(group.folder)}
              />
            ))}
          </>
        )}
        {groups.length === 0 && (
          <p className="px-3 py-2 text-[11px] text-muted-foreground/70">
            {t("sidebar.no_chats")}
          </p>
        )}
      </div>
    </div>
  );
}

/** A quiet section heading — scanned past rather than read. */
function Band({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-3 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {children}
    </div>
  );
}

function FolderGroup({
  group,
  activeSessionId,
  onOpen,
  onDelete,
  onNewChat,
}: {
  group: FolderGroupData;
  activeSessionId: string | null;
  onOpen: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  onNewChat: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const shown = open ? group.chats : group.chats.slice(0, FOLDED);
  const hidden = group.chats.length - shown.length;

  return (
    <section data-testid="workspace-chats-folder" data-folder={group.folder}>
      <div className="group/folder flex items-center gap-1.5 rounded-md px-2 py-1.5">
        <Folder
          aria-hidden
          className="h-3.5 w-3.5 shrink-0"
          style={{ color: folderColor(group.folder || group.label) }}
        />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={group.folder}>
          {group.label}
        </span>
        <button
          type="button"
          onClick={onNewChat}
          title={t("ide_chats.new_chat_here")}
          aria-label={t("ide_chats.new_chat_here")}
          data-testid={`workspace-chats-new-in-${group.folder || "~"}`}
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-background/60 hover:text-foreground focus-visible:opacity-100 group-hover/folder:opacity-100"
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>

      <ul className="space-y-px">
        {shown.map((chat) => (
          <ChatRow
            key={chat.session_id}
            id={chat.session_id}
            title={chat.title || chat.preview}
            active={chat.session_id === activeSessionId}
            onOpen={() => onOpen(chat.session_id)}
            onDelete={() => onDelete(chat.session_id)}
          />
        ))}
      </ul>

      {group.chats.length === 0 && (
        <p className="pl-8 pr-2 text-[11px] text-muted-foreground/50">
          {t("ide_chats.no_chats_here")}
        </p>
      )}
      {(hidden > 0 || open) && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="ml-8 rounded px-2 py-0.5 text-[11px] text-muted-foreground/70 transition-colors hover:text-foreground"
        >
          {open ? t("sidebar.show_less") : t("ide_chats.show_more")}
        </button>
      )}
    </section>
  );
}

function ChatRow({
  id,
  title,
  active,
  onOpen,
  onDelete,
}: {
  id: string;
  title: string;
  active: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const label = title || t("chats_view.new_chat");
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={label}
        data-testid="workspace-chat-row"
        data-session={id}
        className={cn(
          "flex w-full items-center gap-2 rounded-lg py-1.5 pl-8 pr-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          active ? "bg-card text-foreground shadow-sm" : "hover:bg-background/60",
        )}
      >
        <MessageSquare
          aria-hidden
          className={cn("h-3.5 w-3.5 shrink-0", active ? "text-primary" : "text-muted-foreground")}
        />
        <span className="min-w-0 flex-1 truncate text-xs text-foreground">{label}</span>
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        title={t("chats_view.delete")}
        aria-label={t("chats_view.delete")}
        className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded-md bg-card p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive group-hover:block"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </li>
  );
}

interface ChatLike {
  session_id: string;
  title: string;
  preview: string;
  cwd: string;
  updated_ms: number;
}

export interface FolderGroupData {
  /** Absolute path as the backend reported it; "" for the runner's default. */
  folder: string;
  /** What the folder is called — its last segment. */
  label: string;
  chats: ChatLike[];
}

/**
 * Chats grouped by the folder they run in, the open workspace first.
 *
 * The open workspace is ALWAYS the first group even with no chats in it yet:
 * the column has to say where a new chat would land, and a folder that appears
 * only once it has history would leave a fresh workspace looking like it
 * belongs to no folder at all. Everything else follows by recency, so the
 * folder someone touched last is the next one they see.
 *
 * Exported for the tests: the grouping is the contract this column keeps.
 */
export function groupByFolder(
  sessions: readonly ChatLike[],
  workspaceFolder: string,
): FolderGroupData[] {
  const byFolder = new Map<string, ChatLike[]>();
  if (workspaceFolder) byFolder.set(workspaceFolder, []);
  for (const session of sessions) {
    const key = session.cwd ?? "";
    const bucket = byFolder.get(key);
    if (bucket) bucket.push(session);
    else byFolder.set(key, [session]);
  }
  const groups = [...byFolder.entries()].map(([folder, chats]) => ({
    folder,
    label: folderLeaf(folder),
    chats: [...chats].sort((a, b) => b.updated_ms - a.updated_ms),
  }));
  return groups.sort((left, right) => {
    if (left.folder === workspaceFolder) return -1;
    if (right.folder === workspaceFolder) return 1;
    return (right.chats[0]?.updated_ms ?? 0) - (left.chats[0]?.updated_ms ?? 0);
  });
}
