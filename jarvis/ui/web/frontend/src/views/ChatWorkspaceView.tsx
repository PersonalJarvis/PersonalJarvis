/**
 * The chat surface — projects on the left, one conversation in the middle.
 *
 * The layout is the argument. A coding chat has exactly three things on
 * screen: the list you navigate by, the conversation you are having, and the
 * box you type into. Everything the old surface stacked around those — a
 * workspace bar, an agent column, a pane toolbar, a status strip — is either
 * gone or folded into the composer, which is where the answers to "where is
 * this going?" belong anyway.
 *
 * The middle column opens on a starting screen rather than an empty void: the
 * project's name as a question, and four openings that cover what people
 * actually ask a coding agent first. A blank canvas is the hardest place to
 * begin, and this surface is where every session starts.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Compass,
  FileCode2,
  FolderOpen,
  ListChecks,
  PanelRight,
  Sparkles,
  SquareTerminal,
  Wrench,
  X,
} from "lucide-react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { ChatComposer, type ComposerAgent } from "@/components/chat/ChatComposer";
import { ChatLibrarySidebar } from "@/components/chat/ChatLibrarySidebar";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useResizablePane } from "@/hooks/useResizablePane";
import { useEventStore } from "@/store/events";
import { fetchIdeAgents } from "@/lib/agenticIdeApi";
import {
  type ChatProject,
  type ChatRow,
  createChat,
  openProject,
  patchChat,
  projectColor,
} from "@/lib/chatLibraryApi";

interface OpenChat {
  project: ChatProject;
  chat: ChatRow;
}

/**
 * The four openings on the starting screen.
 *
 * Chosen to span what a coding agent is actually asked for on a first turn —
 * understand, build, review, repair — rather than to advertise features. Each
 * one is a real first sentence, so clicking it produces a prompt somebody
 * would have typed.
 */
const OPENERS = [
  {
    icon: Compass,
    tint: "text-sky-400",
    title: "Explore and understand code",
    prompt: "Walk me through how this project is put together and where its seams are.",
  },
  {
    icon: Sparkles,
    tint: "text-violet-400",
    title: "Build a new feature or tool",
    prompt: "I want to add a new feature. Ask me what it should do, then plan it.",
  },
  {
    icon: ListChecks,
    tint: "text-emerald-400",
    title: "Review changes and suggest fixes",
    prompt: "Review the changes on this branch and tell me what you would change.",
  },
  {
    icon: Wrench,
    tint: "text-amber-400",
    title: "Fix problems and errors",
    prompt: "Something is broken. Ask me for the symptom, then find the cause.",
  },
] as const;

/**
 * How much an agent may do without asking, in the user's words.
 *
 * A display list on purpose: the enforcement lives in the risk tiers on the
 * server, and a picker that invented its own names would be describing a
 * policy nothing implements.
 */
const APPROVAL_MODES = ["Ask before acting", "Auto for safe steps", "Full access"] as const;

export function ChatWorkspaceView() {
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [open, setOpen] = useState<OpenChat | null>(null);
  const [lastProject, setLastProject] = useState<ChatProject | null>(null);
  const [picking, setPicking] = useState(false);
  const [pickedPath, setPickedPath] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const [tools, setTools] = useState(true);

  const [agents, setAgents] = useState<ComposerAgent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [modelId, setModelId] = useState<string | null>(null);
  const [approval, setApproval] = useState<string>(APPROVAL_MODES[0]);

  const list = useResizablePane({
    storageKey: "jarvis.chat.listWidth.v1",
    defaultSize: 272,
    min: 220,
    max: 460,
  });

  /*
   * Which coding CLIs this machine actually has.
   *
   * Only installed ones are offered. A picker listing every CLI the registry
   * knows about would let somebody choose one that cannot start, and the
   * failure would arrive after the prompt was written — the worst possible
   * moment to learn that a tool is missing.
   */
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const meta = await fetchIdeAgents();
        if (!alive) return;
        const usable = meta.agents
          .filter((a) => a.installed && (a.kind ?? "cli") === "cli")
          .map<ComposerAgent>((a) => ({ id: a.name, label: a.display_name, models: [] }));
        setAgents(usable);
        setAgentId((current) => current || usable[0]?.id || "");
      } catch {
        // The composer degrades to "no agent chosen" rather than blocking the
        // view: the list is an offer, and the surface is still readable without
        // it (CLAUDE.md §3 — a missing capability never bricks a core path).
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const activeProject = open?.project ?? lastProject;

  const addProject = useCallback(async () => {
    if (!pickedPath) return;
    try {
      const project = await openProject(pickedPath);
      setPicking(false);
      setPickedPath(null);
      setLastProject(project);
      setRefreshToken((n) => n + 1);
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Could not add that folder");
    }
  }, [pickedPath, pushToast]);

  const startChat = useCallback(
    async (project: ChatProject) => {
      try {
        const chat = await createChat(project.id, { agent: agentId, model: modelId });
        setLastProject(project);
        setOpen({ project, chat });
        setRefreshToken((n) => n + 1);
      } catch (error) {
        pushToast("error", error instanceof Error ? error.message : "Could not start that chat");
      }
    },
    [agentId, modelId, pushToast],
  );

  /**
   * Send the first prompt of a chat.
   *
   * The chat is created here if the user typed straight into the starting
   * screen, which is the common path: nobody presses "New chat" first, they
   * type. The title comes from what they wrote, so the row in the sidebar is
   * recognisable the moment it appears.
   */
  const send = useCallback(
    async (text: string) => {
      const project = activeProject;
      if (!project) {
        pushToast("warning", "Pick a project first — a prompt needs a folder to run in.");
        return;
      }
      setBusy(true);
      try {
        let target = open?.chat ?? null;
        if (!target || target.project_id !== project.id) {
          target = await createChat(project.id, { agent: agentId, model: modelId });
        }
        const updated = await patchChat(project.id, target.id, {
          title: target.title || text.slice(0, 48),
        });
        setOpen({ project, chat: updated });
        setRefreshToken((n) => n + 1);
        // The prompt reaches the agent when the conversation layer lands. Said
        // plainly rather than swallowed: a box that accepts text and does
        // nothing visible is the single most confusing state a chat can be in.
        pushToast(
          "info",
          "Saved to this chat. Delivering it to the agent lands with the conversation view.",
        );
      } catch (error) {
        pushToast("error", error instanceof Error ? error.message : "Could not send that");
      } finally {
        setBusy(false);
      }
    },
    [activeProject, agentId, modelId, open, pushToast],
  );

  const composer = useMemo(
    () =>
      activeProject ? (
        <ChatComposer
          projectName={activeProject.name}
          agents={agents}
          agentId={agentId}
          modelId={modelId}
          onAgentChange={setAgentId}
          onModelChange={setModelId}
          approval={approval}
          approvalOptions={APPROVAL_MODES}
          onApprovalChange={setApproval}
          onSend={send}
          onDictate={() => setActiveSection("dictation")}
          onTalk={() => setActiveSection("chats")}
          busy={busy}
        />
      ) : null,
    [activeProject, agentId, agents, approval, busy, modelId, send, setActiveSection],
  );

  return (
    <div className="flex h-full min-h-0 w-full" data-testid="chat-workspace">
      <div style={{ width: list.size }} className="h-full min-h-0 shrink-0">
        <ChatLibrarySidebar
          refreshToken={refreshToken}
          activeChatId={open?.chat.id ?? null}
          onOpenChat={(project, chat) => {
            setLastProject(project);
            setOpen({ project, chat });
          }}
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

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex h-11 shrink-0 items-center gap-2 px-4">
          {open && (
            <>
              <AgentMark agent={open.chat.agent} label={open.chat.agent || "?"} size="sm" />
              <span className="min-w-0 truncate text-xs font-medium">
                {open.chat.title || "New chat"}
              </span>
              <span className="text-muted-foreground/40">·</span>
            </>
          )}
          {activeProject && (
            <span className="flex min-w-0 items-center gap-1.5 truncate text-xs text-muted-foreground">
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: projectColor(activeProject) }}
                aria-hidden
              />
              {activeProject.name}
            </span>
          )}
          <div className="flex-1" />
          {open && (
            <button
              type="button"
              onClick={() => setOpen(null)}
              title="Close this chat"
              aria-label="Close this chat"
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          )}
          <button
            type="button"
            onClick={() => setTools((v) => !v)}
            title={tools ? "Hide quick actions" : "Show quick actions"}
            aria-label={tools ? "Hide quick actions" : "Show quick actions"}
            aria-pressed={tools}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
          >
            <PanelRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto scrollbar-jarvis px-6">
            {activeProject ? (
              <div className="w-full max-w-3xl py-8">
                <div className="mb-6 flex flex-col items-center text-center">
                  <SquareTerminal
                    className="mb-4 h-7 w-7 text-muted-foreground/70"
                    aria-hidden
                  />
                  <h1 className="text-xl font-semibold tracking-tight">
                    What should we build in {activeProject.name}?
                  </h1>
                </div>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
                  {OPENERS.map((opener) => (
                    <button
                      key={opener.title}
                      type="button"
                      onClick={() => void send(opener.prompt)}
                      className="flex h-28 flex-col items-start gap-2 rounded-lg border border-border bg-card/40 p-3 text-left transition-colors hover:border-border hover:bg-accent/40"
                    >
                      <opener.icon className={`h-4 w-4 ${opener.tint}`} aria-hidden />
                      <span className="text-xs leading-snug text-foreground/90">
                        {opener.title}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="max-w-md text-center">
                <FolderOpen
                  className="mx-auto mb-3 h-8 w-8 text-muted-foreground/60"
                  aria-hidden
                />
                <p className="text-sm text-muted-foreground">
                  Add a project folder to start your first chat.
                </p>
                <button
                  type="button"
                  onClick={() => setPicking(true)}
                  className="mt-4 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
                >
                  Add a project
                </button>
              </div>
            )}
          </div>

          {composer}
        </div>

        {/* Quick actions, floating rather than docked: they belong to the chat
            on screen, and a fourth permanent column would take width from the
            one thing this surface exists to show. */}
        {tools && activeProject && (
          <div className="pointer-events-none absolute right-4 top-14 flex flex-col gap-1">
            <QuickAction icon={ListChecks} label="Review" hint="Ctrl+Shift+G" />
            <QuickAction icon={SquareTerminal} label="Terminal" />
            <QuickAction icon={FileCode2} label="Files" hint="Ctrl+P" />
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

function QuickAction({
  icon: Icon,
  label,
  hint,
}: {
  icon: typeof ListChecks;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      className="pointer-events-auto flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      <span>{label}</span>
      {hint && <span className="ml-2 text-[10px] text-muted-foreground/50">{hint}</span>}
    </button>
  );
}
