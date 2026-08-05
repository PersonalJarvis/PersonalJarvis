/**
 * The chat surface — projects on the left, one conversation on screen.
 *
 * This is the shell of the rebuilt section. It owns three things and delegates
 * everything else: which project/chat is open, how a project gets added, and
 * how a new chat is started. The list is `ChatLibrarySidebar`; the conversation
 * itself arrives in the next wave, reading the coding CLI's own transcript
 * rather than scraping a terminal.
 *
 * The header is deliberately the only chrome. The old surface stacked a
 * workspace bar, an agent column, a pane toolbar and a status strip around the
 * content, and the result was that the thing you came for — the conversation —
 * had the least room on screen.
 */
import { useCallback, useState } from "react";
import { FolderOpen, MessagesSquare, X } from "lucide-react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { ChatLibrarySidebar } from "@/components/chat/ChatLibrarySidebar";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useResizablePane } from "@/hooks/useResizablePane";
import { useEventStore } from "@/store/events";
import {
  type ChatProject,
  type ChatRow,
  createChat,
  openProject,
  projectColor,
} from "@/lib/chatLibraryApi";

/** What is on screen in the main column. */
interface OpenChat {
  project: ChatProject;
  chat: ChatRow;
}

export function ChatWorkspaceView() {
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState<OpenChat | null>(null);
  const [picking, setPicking] = useState(false);
  const [pickedPath, setPickedPath] = useState<string | null>(null);
  /*
   * Bumped whenever something changes the library from OUT here — adding a
   * project, starting a chat. The sidebar remounts on the new key and reloads,
   * which is a great deal simpler than threading a refresh handle up through
   * it, and cheap because the reload is two small requests.
   */
  const [libraryKey, setLibraryKey] = useState(0);

  // The list column is drag-resizable and remembers its width, like every other
  // seam in the app.
  const list = useResizablePane({
    storageKey: "jarvis.chat.listWidth.v1",
    defaultSize: 288,
    min: 220,
    max: 480,
  });

  const addProject = useCallback(async () => {
    if (!pickedPath) return;
    try {
      await openProject(pickedPath);
      setPicking(false);
      setPickedPath(null);
      setLibraryKey((n) => n + 1);
    } catch (error) {
      pushToast(
        "error",
        error instanceof Error ? error.message : "Could not add that folder",
      );
    }
  }, [pickedPath, pushToast]);

  const startChat = useCallback(
    async (project: ChatProject) => {
      try {
        // The agent is asked for in the next wave's new-chat dialog. Until it
        // exists a chat is created unassigned rather than silently pinned to
        // one CLI — an empty agent shows a neutral mark, a wrong one would lie.
        const chat = await createChat(project.id, { agent: "" });
        setLibraryKey((n) => n + 1);
        setOpen({ project, chat });
      } catch (error) {
        pushToast(
          "error",
          error instanceof Error ? error.message : "Could not start that chat",
        );
      }
    },
    [pushToast],
  );

  return (
    <div className="flex h-full min-h-0 w-full" data-testid="chat-workspace">
      <div style={{ width: list.size }} className="h-full min-h-0 shrink-0">
        <ChatLibrarySidebar
          key={libraryKey}
          activeChatId={open?.chat.id ?? null}
          onOpenChat={(project, chat) => setOpen({ project, chat })}
          onNewChat={(project) => void startChat(project)}
          onAddProject={() => setPicking(true)}
        />
      </div>

      <PaneResizer
        orientation="vertical"
        onPointerDown={list.startResize}
        onDoubleClick={list.reset}
        onNudge={list.nudge}
        active={list.isResizing}
        title="Drag to resize the chat list — double-click to reset"
      />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {open ? (
          <>
            <header className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-3">
              <AgentMark agent={open.chat.agent} label={open.chat.agent || "?"} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold">
                  {open.chat.title || "New chat"}
                </div>
                <div className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: projectColor(open.project) }}
                    aria-hidden
                  />
                  {open.project.name}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(null)}
                title="Close this chat"
                aria-label="Close this chat"
                className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </header>
            <div className="flex min-h-0 flex-1 items-center justify-center px-8 text-center">
              <div className="max-w-md">
                <MessagesSquare
                  className="mx-auto mb-3 h-8 w-8 text-muted-foreground/60"
                  aria-hidden
                />
                <p className="text-sm text-muted-foreground">
                  Nothing has been said in this chat yet.
                </p>
                <p className="mt-1 text-xs text-muted-foreground/70">
                  The conversation view and the composer land in the next wave — this
                  chat already exists and will keep whatever is said in it.
                </p>
              </div>
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center px-8 text-center">
            <div className="max-w-md">
              <FolderOpen
                className="mx-auto mb-3 h-8 w-8 text-muted-foreground/60"
                aria-hidden
              />
              <p className="text-sm text-muted-foreground">
                Pick a chat on the left, or add a project folder to start one.
              </p>
            </div>
          </div>
        )}
      </div>

      {picking && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-6">
          <div className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
            <header className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">Add a project folder</h2>
              <button
                type="button"
                onClick={() => {
                  setPicking(false);
                  setPickedPath(null);
                }}
                aria-label="Cancel"
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <FolderPicker selected={pickedPath} onSelect={setPickedPath} />
            </div>
            <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={() => {
                  setPicking(false);
                  setPickedPath(null);
                }}
                className="rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={!pickedPath}
                onClick={() => void addProject()}
                className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
              >
                Add project
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
