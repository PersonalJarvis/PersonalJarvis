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
  FolderOpen,
  LayoutGrid,
  ListChecks,
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
import {
  addTerminal,
  fetchIdeAgents,
  fetchIdeState,
  promptTerminal,
  startIdeSession,
} from "@/lib/agenticIdeApi";
import { ChatActivity } from "@/components/chat/ChatActivity";
import { VoiceBubble } from "@/components/agentic/VoiceBubble";
import { getWSClient } from "@/hooks/useWebSocket";
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

export function ChatWorkspaceView() {
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [open, setOpen] = useState<OpenChat | null>(null);
  const [lastProject, setLastProject] = useState<ChatProject | null>(null);
  const [picking, setPicking] = useState(false);
  const [pickedPath, setPickedPath] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  // The spoken conversation, as a floating orb over this surface. Held here
  // rather than inside the composer: the conversation belongs to the app, and
  // a bubble mounted inside a component that re-renders per keystroke would
  // reset the orb mid-sentence.
  const [talking, setTalking] = useState(false);

  const [agents, setAgents] = useState<ComposerAgent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [modelId, setModelId] = useState<string | null>(null);

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
  /**
   * Make sure this chat has a LIVE agent, and say which pane it is.
   *
   * Three cases, in the order they actually happen: the chat is already bound
   * to a running pane and we use it; the workspace is open on this project and
   * we add a pane to it; nothing is open and we start one. The chat records
   * the pane it got, so reopening it later finds the terminal that already
   * holds the conversation instead of starting a second agent beside it.
   */
  /**
   * Wait for a freshly opened pane to be able to receive a prompt.
   *
   * A pane is born ``pending``: the process is spawned but its pseudo-terminal
   * has not attached yet, and a prompt sent into that window is refused
   * outright — which is exactly what happened, and what the user saw as "T2 is
   * not running right now (status: pending) — nothing was sent."
   *
   * So the send waits for ``live`` instead of racing it. Generously bounded: a
   * cold coding CLI on a large repository takes seconds, not milliseconds, and
   * a timeout that fires early would reintroduce the same failure with a nicer
   * message. A pane that dies on the way up says so rather than timing out.
   */
  const waitForPane = useCallback(async (name: string): Promise<void> => {
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      const state = await fetchIdeState();
      const pane = state.session?.terminals.find((t) => t.name === name);
      if (!pane) throw new Error(`${name} disappeared before it could start.`);
      if (pane.status === "live") return;
      if (pane.status === "error" || pane.status === "exited") {
        throw new Error(pane.error || `${name} stopped before it could start.`);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
    throw new Error(`${name} did not finish starting. Open the terminal grid to see why.`);
  }, []);

  /**
   * Make sure this chat has a LIVE agent, and say which pane it is.
   *
   * Three cases, in the order they actually happen: the chat is already bound
   * to a running pane and we use it; the workspace is open on this project and
   * we add a pane to it; nothing is open and we start one. The chat records
   * the pane it got, so reopening it finds the terminal that already holds the
   * conversation instead of starting a second agent beside it.
   */
  const ensureAgent = useCallback(
    async (project: ChatProject, chat: ChatRow): Promise<string> => {
      const state = await fetchIdeState();
      const session = state.session;
      const sameFolder =
        session && session.folder.toLowerCase() === project.path.toLowerCase();

      if (sameFolder && chat.terminal) {
        const live = session.terminals.find((t) => t.name === chat.terminal);
        // A pane that is still coming up is ours too — waiting for it beats
        // starting a second agent beside it.
        if (live && live.status !== "exited" && live.status !== "error") {
          await waitForPane(live.name);
          return live.name;
        }
      }

      const agent = chat.agent || agentId || agents[0]?.id;
      if (!agent) throw new Error("No coding CLI is installed on this machine.");

      const before = sameFolder ? session.terminals.map((t) => t.name) : [];
      const next = sameFolder
        ? await addTerminal({ agent })
        : (await startIdeSession(project.path, [{ agent }])).session;
      if (!next) throw new Error("The workspace closed while starting the agent.");

      // The pane that was NOT there a moment ago is ours. Asked this way rather
      // than by trusting a returned index, because the workspace is shared —
      // another surface can add a pane between these two calls.
      const fresh =
        next.terminals.find((t) => !before.includes(t.name)) ??
        next.terminals[next.terminals.length - 1];
      if (!fresh) throw new Error("The agent started but reported no terminal.");

      await waitForPane(fresh.name);
      await patchChat(project.id, chat.id, { terminal: fresh.name });
      return fresh.name;
    },
    [agentId, agents, waitForPane],
  );

  /**
   * Send a prompt — for real.
   *
   * The chat is created here if the user typed straight into the starting
   * screen, which is the common path: nobody presses "New chat" first, they
   * type. Then an agent is started if this chat has none, and the prompt goes
   * into it. What the agent does next is on screen a second later, because the
   * feed below is already following that pane.
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
        const terminal = await ensureAgent(project, target);
        await promptTerminal(terminal, text);
        const updated = await patchChat(project.id, target.id, {
          title: target.title || text.slice(0, 48),
          terminal,
        });
        setOpen({ project, chat: updated });
        setRefreshToken((n) => n + 1);
      } catch (error) {
        pushToast("error", error instanceof Error ? error.message : "Could not send that");
      } finally {
        setBusy(false);
      }
    },
    [activeProject, agentId, ensureAgent, modelId, open, pushToast],
  );

  /**
   * Bring a chat back to life when it is opened.
   *
   * Closing the app stops every agent — deliberately, an unwatched agent burns
   * tokens invisibly — so a chat from yesterday has no pane. Clicking it starts
   * one again in the same folder with the same CLI, which is what "open the
   * chat" means to somebody who left it running.
   *
   * A chat that already has a live pane costs nothing here: `ensureAgent`
   * recognises it and returns. Failures are quiet on purpose — the user asked
   * to LOOK at a conversation, and a red box for an agent they have not typed
   * to yet would be noise. The next send reports honestly.
   */
  const reopen = useCallback(
    async (project: ChatProject, chat: ChatRow) => {
      setStarting(true);
      try {
        const terminal = await ensureAgent(project, chat);
        setOpen((current) =>
          current && current.chat.id === chat.id
            ? { project, chat: { ...current.chat, terminal } }
            : current,
        );
      } catch {
        /* see above — the send path is where this is reported */
      } finally {
        setStarting(false);
      }
    },
    [ensureAgent],
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
          onSend={send}
          onDictate={() => {
            // Speech lands in whichever field has the caret (the app-wide
            // dictation focus tracker decides that), so the box is focused
            // first — a dictation started from a BUTTON has already moved
            // focus onto the button by the time the transcript arrives.
            document
              .querySelector<HTMLTextAreaElement>('[data-dictation-target="true"]')
              ?.focus();
            getWSClient()?.send({
              type: "command",
              action: "stt_dictate",
              payload: { mode: "start" },
            });
          }}
          onTalk={() => setTalking(true)}
          busy={busy || starting}
        />
      ) : null,
    [activeProject, agentId, agents, busy, modelId, send, starting],
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
            void reopen(project, chat);
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
            data-testid="open-classic-grid"
            onClick={() => setActiveSection("agentic-ide-classic")}
            title="Open the classic terminal grid"
            className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
          >
            <LayoutGrid className="h-3.5 w-3.5" aria-hidden />
            Terminal grid
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col">
          {open?.chat.terminal ? (
            <ChatActivity terminal={open.chat.terminal} />
          ) : (
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
          )}

          {composer}
        </div>

      </div>

      <VoiceBubble
        open={talking}
        onClose={() => setTalking(false)}
        promptTarget={open?.chat.terminal ?? ""}
      />

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
