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
 * The middle column belongs to the agent. Before a prompt it holds the
 * project's name as a question and says where the answer will appear; from the
 * first prompt on it IS the agent, live, in that same rectangle. One surface in
 * two states, never a menu of things to click instead.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderOpen, LayoutGrid, Loader2, SquareTerminal, X } from "lucide-react";

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
import { AgenticTerminal } from "@/components/agentic/AgenticTerminal";
import { ChatStream } from "@/components/chat/ChatStream";
import { VoiceBubble } from "@/components/agentic/VoiceBubble";
import { getWSClient } from "@/hooks/useWebSocket";
import {
  type ChatProject,
  type ChatRow,
  createChat,
  openProject,
  openScratchProject,
  patchChat,
  projectColor,
} from "@/lib/chatLibraryApi";

interface OpenChat {
  project: ChatProject;
  chat: ChatRow;
}

/**
 * The last segment of a path, whichever separator this machine uses.
 *
 * Read off the path rather than guessed from the browser: the folder lives on
 * the machine the BACKEND runs on, which is not necessarily the one this window
 * is open on. A path that ends in a separator still answers with its folder.
 */
function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

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
  // Which workspace the live pane belongs to. Sent with the socket so a
  // keystroke reaches the pane it was typed into: several workspaces can be
  // open and the front one changes while sockets are alive.
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
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
   * Start a chat without choosing a folder first.
   *
   * The agent still runs somewhere — the home directory — because a coding CLI
   * is a process with a working directory and there is no such thing as
   * nowhere. What the user is spared is the decision: a question, a quick
   * script, "which of these two files differs" are all worth asking without
   * first naming a project to ask them in.
   */
  const startSession = useCallback(async () => {
    try {
      const project = await openScratchProject();
      await startChat(project);
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Could not start a session");
    }
  }, [pushToast, startChat]);

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
      if (session) setWorkspaceId(session.id);
      const sameFolder =
        session && session.folder.toLowerCase() === project.path.toLowerCase();

      if (sameFolder && chat.terminal) {
        const live = session.terminals.find((t) => t.name === chat.terminal);
        // A pane that is still coming up is ours too — waiting for it beats
        // starting a second agent beside it.
        if (live && live.status !== "exited" && live.status !== "error") {
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

      await patchChat(project.id, chat.id, { terminal: fresh.name });
      return fresh.name;
    },
    [agentId, agents],
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
        /*
         * Mount the pane BEFORE waiting for it, then prompt.
         *
         * The order is the fix. A coding CLI is spawned when something attaches
         * to its pseudo-terminal, so a send that waited for "live" before
         * putting the pane on screen was waiting for a process its own silence
         * was preventing — three panes sat at "pending" and every prompt was
         * refused. Showing it first is what starts it.
         */
        const updated = await patchChat(project.id, target.id, {
          title: target.title || text.slice(0, 48),
          terminal,
        });
        setOpen({ project, chat: updated });
        setRefreshToken((n) => n + 1);
        await waitForPane(terminal);
        await promptTerminal(terminal, text);
      } catch (error) {
        pushToast("error", error instanceof Error ? error.message : "Could not send that");
      } finally {
        setBusy(false);
      }
    },
    [activeProject, agentId, ensureAgent, modelId, open, pushToast, waitForPane],
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

  // Something was asked for and the agent is not on screen yet — a cold coding
  // CLI on a large repository takes seconds. Said once here so the empty fold
  // and the composer cannot disagree about whether anything is happening.
  const waiting = busy || starting;

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
          busy={waiting}
        />
      ) : null,
    [activeProject, agentId, agents, modelId, send, waiting],
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
          onNewSession={() => void startSession()}
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
              <AgentMark
                agent={open.chat.agent}
                label={open.chat.agent || "?"}
                size="sm"
                variant="plain"
              />
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
            /*
             * The REAL pane, not a rendering of one.
             *
             * A coding CLI is only spawned when something attaches to its
             * pseudo-terminal — the socket IS the start signal. A surface that
             * merely polled for output therefore watched a process that never
             * began: three panes sat at "pending" forever and every prompt was
             * refused, which is exactly what happened here. Mounting the pane
             * starts the agent AND shows what it is doing, and it brings the
             * years of replay, scroll and resize fixes with it.
             */
            <div className="relative flex min-h-0 flex-1 flex-col">
              {/*
                The pane runs, and is not shown.

                A coding agent lives only while something is attached to its
                pseudo-terminal, so the terminal cannot go away — but nobody
                should have to READ one to follow their own agent. It is kept
                mounted underneath at full size (xterm needs real geometry to
                pick its columns; a display:none pane reports zero and the CLI
                wraps its output at nothing) and covered by the conversation.
                Hidden from assistive technology too: the same words are right
                there in the stream, in a better order.
              */}
              <div
                className="pointer-events-none absolute inset-0 -z-10 overflow-hidden opacity-0"
                aria-hidden
              >
                <AgenticTerminal
                  key={open.chat.terminal}
                  name={open.chat.terminal}
                  workspaceId={workspaceId ?? undefined}
                  displayName={open.chat.agent || "Agent"}
                  appearance="dark"
                  fontSize={13}
                  active
                />
              </div>
              <ChatStream terminal={open.chat.terminal} />
            </div>
          ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto scrollbar-jarvis px-6">
            {activeProject ? (
              /*
               * The same fold the pane will occupy, holding its place.
               *
               * Nothing to click here on purpose: the only thing to do on this
               * screen is say what you want, and the box below already asks
               * for it. What replaces this line is the agent itself, in this
               * exact spot — so the wait reads as "it is coming here", not as
               * "something is missing".
               */
              <div className="flex max-w-md flex-col items-center gap-3 py-8 text-center">
                {waiting ? (
                  <Loader2
                    className="h-7 w-7 animate-spin text-muted-foreground/70"
                    aria-hidden
                  />
                ) : (
                  <SquareTerminal className="h-7 w-7 text-muted-foreground/70" aria-hidden />
                )}
                <h1 className="text-xl font-semibold tracking-tight">
                  What should we build in {activeProject.name}?
                </h1>
                <p className="text-xs text-muted-foreground">
                  {waiting
                    ? "Starting the agent — it takes over this space in a moment."
                    : "Type below. The agent opens right here and works in front of you."}
                </p>
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
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
              <FolderPicker selected={pickedPath} onSelect={setPickedPath} />
            </div>
            {/*
              What is about to be added, in words, above the button that adds it.

              The picker deliberately does not report its own selection — it
              expects the surface around it to, because the answer belongs next
              to the control that acts on it. This dialog did not, so clicking
              a folder produced no visible change anywhere and the only feedback
              was a button going from 40% to 100% opacity. That is what "I
              cannot add a folder" looks like from the outside.
            */}
            <div className="flex min-w-0 items-center gap-2 border-t border-border px-4 py-2 text-xs">
              {pickedPath ? (
                <>
                  <span className="shrink-0 text-muted-foreground">Selected</span>
                  <code className="min-w-0 truncate font-mono text-foreground">
                    {pickedPath}
                  </code>
                </>
              ) : (
                <span className="text-muted-foreground">
                  Pick a folder above — click one to select it.
                </span>
              )}
            </div>
            <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={() => {
                  setPicking(false);
                  setPickedPath(null);
                }}
                className="rounded-md px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="confirm-add-project"
                disabled={!pickedPath}
                onClick={() => void addProject()}
                className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                {pickedPath ? `Add ${folderName(pickedPath)}` : "Add project"}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
