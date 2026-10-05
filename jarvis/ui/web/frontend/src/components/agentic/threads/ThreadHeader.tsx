import { useEffect, useRef, useState } from "react";
import {
  ChevronDown, FileDiff, FolderOpen, GitCommitHorizontal, GitPullRequest, Globe, Loader2, Pencil, Play, Plus,
  SquareCode, SquareTerminal, Trash2, Upload,
} from "lucide-react";
import { patchAgentChatSession } from "@/lib/agentChatApi";
import { fetchProjectLaunchers, openProjectIn, revealProject, type ProjectLaunchers } from "@/lib/chatLibraryApi";
import { commitAll, openPullRequest, pushBranch } from "@/lib/gitApi";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useGitInfo } from "./ThreadBranchBar";
import { ThreadMenuHeading, ThreadMenuItem, ThreadMenuSeparator, ThreadPopover } from "./ThreadPopover";
import { deleteProjectAction, readProjectActions, saveProjectAction, type ProjectAction } from "./projectActions";
import { useThreadChatStore } from "./threadModel";

const PILL = "flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const ICON_BUTTON = "flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const FILE_MANAGER_LABEL = (() => {
  const platform = typeof navigator === "undefined" ? "" : navigator.userAgent;
  if (/Windows/i.test(platform)) return "Show in Explorer";
  if (/Mac OS X|Macintosh/i.test(platform)) return "Show in Finder";
  return "Show in file manager";
})();

const LAST_EDITOR_KEY = "jarvis.ide.threadOpenIn.v1";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** "Add action", or the project's first action with the rest behind a chevron. */
function ActionsControl({ projectId, onRun }: { projectId: string; onRun: (action: ProjectAction) => void }) {
  const [actions, setActions] = useState<ProjectAction[]>(() => readProjectActions(projectId));
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState<ProjectAction | null>(null);
  const anchor = useRef<HTMLDivElement | null>(null);
  useEffect(() => { setActions(readProjectActions(projectId)); }, [projectId]);
  const first = actions[0];
  const startNew = () => { setEditing({ id: `action-${Date.now().toString(36)}`, name: "", command: "" }); setMenu(true); };
  const save = () => {
    if (!editing || !editing.name.trim() || !editing.command.trim()) return;
    setActions(saveProjectAction(projectId, { ...editing, name: editing.name.trim(), command: editing.command.trim() }));
    setEditing(null);
    setMenu(false);
  };
  return <div ref={anchor} className="flex items-center">
    {first
      ? <div className="flex items-center">
        <button type="button" className={cn(PILL, "rounded-r-none")} onClick={() => onRun(first)} title={first.command} data-testid="thread-action-run">
          <Play aria-hidden className="h-3 w-3" />{first.name}
        </button>
        <button type="button" aria-label="More actions" aria-haspopup="menu" aria-expanded={menu}
          className={cn(PILL, "rounded-l-none border-l-0 px-1")} onClick={() => { setEditing(null); setMenu(!menu); }}>
          <ChevronDown aria-hidden className="h-3 w-3" />
        </button>
      </div>
      : <button type="button" className={cn(PILL, "border-transparent text-muted-foreground hover:text-foreground")} onClick={startNew} data-testid="thread-add-action">
        <Plus aria-hidden className="h-3 w-3" />Add action
      </button>}
    <ThreadPopover anchor={anchor} open={menu} onClose={() => { setMenu(false); setEditing(null); }} label="Project actions" align="end" width={320}>
      {editing
        ? <form className="space-y-2 p-2" onSubmit={(event) => { event.preventDefault(); save(); }}>
          <p className="text-sm font-medium text-foreground-strong">{actions.some((row) => row.id === editing.id) ? "Edit action" : "New action"}</p>
          <p className="text-xs text-muted-foreground">A command this project runs often. It opens in the terminal drawer.</p>
          <label className="block text-xs text-muted-foreground">Name
            <input autoFocus value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} placeholder="Dev server" maxLength={40}
              className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          </label>
          <label className="block text-xs text-muted-foreground">Command
            <input value={editing.command} onChange={(event) => setEditing({ ...editing, command: event.target.value })} placeholder="npm run dev"
              className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 font-mono text-xs text-foreground outline-none focus:ring-2 focus:ring-ring" />
          </label>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setEditing(null)} className="rounded-md px-2.5 py-1 text-sm text-muted-foreground hover:bg-secondary">Cancel</button>
            <button type="submit" disabled={!editing.name.trim() || !editing.command.trim()} className="rounded-md bg-accent px-2.5 py-1 text-sm text-accent-foreground disabled:opacity-40">Save</button>
          </div>
        </form>
        : <div role="menu">
          {actions.map((action) => <div key={action.id} className="group/action flex items-center">
            <div className="min-w-0 flex-1"><ThreadMenuItem icon={<Play className="h-3.5 w-3.5" />} label={action.name}
              hint={<span className="font-mono">{action.command.length > 22 ? `${action.command.slice(0, 21)}…` : action.command}</span>}
              onSelect={() => { setMenu(false); onRun(action); }} /></div>
            <button type="button" aria-label={`Edit ${action.name}`} onClick={() => setEditing(action)} className="rounded p-1 text-muted-foreground opacity-0 hover:text-foreground group-hover/action:opacity-100"><Pencil className="h-3.5 w-3.5" /></button>
            <button type="button" aria-label={`Delete ${action.name}`} onClick={() => setActions(deleteProjectAction(projectId, action.id))} className="rounded p-1 text-muted-foreground opacity-0 hover:text-destructive group-hover/action:opacity-100"><Trash2 className="h-3.5 w-3.5" /></button>
          </div>)}
          <ThreadMenuSeparator />
          <ThreadMenuItem icon={<Plus className="h-3.5 w-3.5" />} label="Add action" onSelect={startNew} />
        </div>}
    </ThreadPopover>
  </div>;
}

/** "Open" in the editor last used, with every editor and the file manager behind the chevron. */
function OpenInControl({ projectId }: { projectId: string }) {
  const pushToast = useEventStore((state) => state.pushToast);
  const [launchers, setLaunchers] = useState<ProjectLaunchers | null>(null);
  const [menu, setMenu] = useState(false);
  const anchor = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    let live = true;
    fetchProjectLaunchers(projectId).then((next) => { if (live) setLaunchers(next); }).catch(() => { if (live) setLaunchers(null); });
    return () => { live = false; };
  }, [projectId]);
  const editors = launchers?.editors ?? [];
  let remembered = "";
  try { remembered = localStorage.getItem(LAST_EDITOR_KEY) ?? ""; } catch { /* blocked storage: the first editor leads */ }
  const preferred = editors.find((editor) => editor.id === remembered) ?? editors[0];
  const open = async (target: string) => {
    setMenu(false);
    try {
      if (target === "files") { await revealProject(projectId); return; }
      if (target === "remote" && launchers?.remote_url) { await openExternalUrl(launchers.remote_url); return; }
      await openProjectIn(projectId, target);
      try { localStorage.setItem(LAST_EDITOR_KEY, target); } catch { /* a convenience only */ }
    } catch (error) {
      pushToast("error", errorText(error));
    }
  };
  if (!launchers) return null;
  return <div ref={anchor} className="flex items-center">
    <button type="button" className={cn(PILL, "rounded-r-none")} data-testid="thread-open-in"
      onClick={() => void open(preferred ? preferred.id : "files")} title={preferred ? `Open in ${preferred.label}` : FILE_MANAGER_LABEL}>
      {preferred ? <SquareCode aria-hidden className="h-3.5 w-3.5" /> : <FolderOpen aria-hidden className="h-3.5 w-3.5" />}Open
    </button>
    <button type="button" aria-label="Open in…" aria-haspopup="menu" aria-expanded={menu} className={cn(PILL, "rounded-l-none border-l-0 px-1")} onClick={() => setMenu(!menu)}>
      <ChevronDown aria-hidden className="h-3 w-3" />
    </button>
    <ThreadPopover anchor={anchor} open={menu} onClose={() => setMenu(false)} label="Open in" align="end" width={240}>
      <div role="menu">
        {editors.map((editor) => <ThreadMenuItem key={editor.id} icon={<SquareCode className="h-3.5 w-3.5" />} label={editor.label}
          selected={editor.id === preferred?.id} onSelect={() => void open(editor.id)} />)}
        {launchers.file_manager && <ThreadMenuItem icon={<FolderOpen className="h-3.5 w-3.5" />} label={FILE_MANAGER_LABEL} onSelect={() => void open("files")} />}
        {launchers.remote_url && <ThreadMenuItem icon={<Globe className="h-3.5 w-3.5" />} label={launchers.remote_label ? `Open on ${launchers.remote_label}` : "Open the remote"} onSelect={() => void open("remote")} />}
      </div>
    </ThreadPopover>
  </div>;
}

/** Commit, push and open a pull request for the thread's folder. */
function GitControl({ folder, version, defaultMessage }: { folder: string; version: number; defaultMessage: string }) {
  const pushToast = useEventStore((state) => state.pushToast);
  const { info, refresh } = useGitInfo(folder);
  const [menu, setMenu] = useState(false);
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState<string | null>(null);
  const anchor = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { if (menu) { refresh(); setMessage((current) => current || defaultMessage); } }, [menu, refresh, defaultMessage]);
  useEffect(() => { if (version > 0) refresh(); }, [version, refresh]);
  if (!info?.is_repo) return null;
  const changed = info.changes.length;
  const hasRemote = info.remotes.length > 0;
  const label = info.dirty ? (hasRemote ? "Commit & push" : "Commit") : info.ahead > 0 ? "Push" : "Create PR";
  const run = async (what: string, work: () => Promise<void>) => {
    setWorking(what);
    try { await work(); }
    catch (error) { pushToast("error", errorText(error)); }
    finally { setWorking(null); refresh(); }
  };
  const commit = (push: boolean) => run(push ? "commit-push" : "commit", async () => {
    const text = message.trim();
    if (!text) throw new Error("Write a commit message first.");
    const result = await commitAll(folder, text);
    if (push) await pushBranch(folder);
    pushToast("success", push ? `Committed ${result.commit.slice(0, 7)} and pushed.` : `Committed ${result.commit.slice(0, 7)}.`);
    setMessage("");
    setMenu(false);
  });
  const push = () => run("push", async () => {
    const result = await pushBranch(folder);
    pushToast("success", `Pushed to ${result.upstream}.`);
    setMenu(false);
  });
  const pullRequest = () => run("pr", async () => {
    const result = await openPullRequest(folder, { title: message.trim() || defaultMessage });
    pushToast("success", "Pull request opened.");
    setMenu(false);
    if (result.url) void openExternalUrl(result.url);
  });
  return <>
    <button ref={anchor} type="button" className={PILL} aria-haspopup="dialog" aria-expanded={menu} onClick={() => setMenu(!menu)} data-testid="thread-git">
      {working ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <GitCommitHorizontal aria-hidden className="h-3.5 w-3.5" />}
      {label}
      <ChevronDown aria-hidden className="h-3 w-3" />
    </button>
    <ThreadPopover anchor={anchor} open={menu} onClose={() => setMenu(false)} label="Git" align="end" width={340}>
      <div className="space-y-2 p-2">
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="truncate font-mono">{info.branch || "detached"}</span>
          <span className="shrink-0 tabular-nums">
            {changed > 0 ? `${changed} changed` : "clean"}
            {(info.insertions > 0 || info.deletions > 0) && <> · <span className="text-success">+{info.insertions}</span> <span className="text-destructive">−{info.deletions}</span></>}
            {info.ahead > 0 && ` · ${info.ahead} ahead`}
            {info.behind > 0 && ` · ${info.behind} behind`}
          </span>
        </div>
        {info.dirty && <>
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} rows={3} aria-label="Commit message" placeholder="Commit message"
            className="w-full resize-none rounded-md border border-input bg-background px-2 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          <div className="flex justify-end gap-2">
            <button type="button" disabled={working !== null} onClick={() => void commit(false)} className="rounded-md border border-border px-2.5 py-1 text-sm hover:bg-secondary disabled:opacity-50">Commit</button>
            {hasRemote && <button type="button" disabled={working !== null} onClick={() => void commit(true)} className="rounded-md bg-accent px-2.5 py-1 text-sm text-accent-foreground disabled:opacity-50">Commit &amp; push</button>}
          </div>
        </>}
        {!info.dirty && <p className="text-sm text-muted-foreground">{info.ahead > 0 ? `${info.ahead} commit${info.ahead === 1 ? "" : "s"} not pushed yet.` : "Nothing to commit."}</p>}
        <div role="menu" className="border-t border-border pt-1">
          <ThreadMenuHeading>More</ThreadMenuHeading>
          <ThreadMenuItem icon={<Upload className="h-3.5 w-3.5" />} label="Push" disabled={!hasRemote || working !== null || (info.ahead === 0 && Boolean(info.upstream))} onSelect={() => void push()} />
          <ThreadMenuItem icon={<GitPullRequest className="h-3.5 w-3.5" />} label="Create pull request" hint={info.gh_available ? undefined : "needs gh"}
            disabled={!hasRemote || !info.gh_available || working !== null || info.branch === info.default_branch} onSelect={() => void pullRequest()} />
        </div>
      </div>
    </ThreadPopover>
  </>;
}

/**
 * The bar over a thread: where it lives and what it is called on the left,
 * the project's actions, "Open", the git actions and the two drawer
 * switches on the right.
 */
export function ThreadHeader({
  projectId,
  projectName,
  folder,
  title,
  sessionId,
  terminalOpen,
  onToggleTerminal,
  diffOpen,
  onToggleDiff,
  gitVersion,
  onRunAction,
}: {
  projectId: string;
  projectName: string;
  folder: string;
  title: string;
  sessionId: string | null;
  terminalOpen: boolean;
  onToggleTerminal: () => void;
  diffOpen: boolean;
  onToggleDiff: () => void;
  /** Changes when a turn ends, so the git button reads the folder again. */
  gitVersion: number;
  onRunAction: (action: ProjectAction) => void;
}) {
  const pushToast = useEventStore((state) => state.pushToast);
  const [renaming, setRenaming] = useState<string | null>(null);
  const rename = async (next: string) => {
    setRenaming(null);
    const clean = next.trim();
    if (!sessionId || !clean || clean === title) return;
    try {
      await patchAgentChatSession(sessionId, { title: clean });
      await useThreadChatStore.getState().loadSessions();
    } catch (error) {
      pushToast("error", errorText(error));
    }
  };
  return <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4" data-testid="thread-header">
    <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
      <span className="shrink-0 truncate text-muted-foreground">{projectName}</span>
      <span aria-hidden className="text-faint-foreground">/</span>
      {renaming !== null
        ? <input autoFocus aria-label="Thread name" value={renaming} maxLength={120}
          onChange={(event) => setRenaming(event.target.value)} onBlur={() => void rename(renaming)}
          onKeyDown={(event) => { if (event.key === "Enter") void rename(renaming); if (event.key === "Escape") { event.stopPropagation(); setRenaming(null); } }}
          className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-0.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
        : <h1 className={cn("min-w-0 truncate font-medium text-foreground-strong", sessionId && "cursor-text")} title={sessionId ? `${title} — double-click to rename` : title}
          onDoubleClick={() => { if (sessionId) setRenaming(title); }} data-testid="thread-title">{title}</h1>}
    </div>
    <div className="flex shrink-0 items-center gap-1.5">
      <ActionsControl projectId={projectId} onRun={onRunAction} />
      <OpenInControl projectId={projectId} />
      <GitControl folder={folder} version={gitVersion} defaultMessage={title === "New thread" ? "" : title} />
      <span aria-hidden className="mx-0.5 h-4 w-px bg-border" />
      <button type="button" aria-label={terminalOpen ? "Hide terminal" : "Show terminal"} title="Terminal" aria-pressed={terminalOpen}
        onClick={onToggleTerminal} className={cn(ICON_BUTTON, terminalOpen && "bg-secondary text-foreground")} data-testid="thread-terminal-toggle">
        <SquareTerminal className="h-4 w-4" />
      </button>
      <button type="button" aria-label={diffOpen ? "Hide changes" : "Show changes"} title="Changes" aria-pressed={diffOpen}
        onClick={onToggleDiff} className={cn(ICON_BUTTON, diffOpen && "bg-secondary text-foreground")} data-testid="thread-diff-toggle">
        <FileDiff className="h-4 w-4" />
      </button>
    </div>
  </header>;
}
