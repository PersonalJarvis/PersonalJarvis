import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Check, ChevronDown, ChevronRight, Copy, CopyPlus, Folder, FolderGit2, GitBranch, FolderOpen, FolderPlus, Globe, Loader2, Mic, MoreHorizontal, OctagonPause, Pencil, Pin, PinOff, Plus, Server, SquareCode, Trash2, X, type LucideIcon } from "lucide-react";
import { ChatLibraryError, deleteProject, openProject, patchProject, reorderProjects, revealProject, fetchProjectLaunchers, openProjectIn, type ProjectLaunchers } from "@/lib/chatLibraryApi";
import { robustCopy } from "@/lib/clipboard";
import { useComputerChoices } from "@/hooks/useComputers";
import { addTerminal, fetchWorkspacePanes, interruptTerminal, placeWorkspace, removeWorkspace, renameWorkspace, startIdeSession, IdeApiError, reorderWorkspaces, type IdeProject, type ProjectWorkspace, type WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";

const EXPANSION_KEY = "jarvis.ide.projectExpansion.v1";
const WORKSPACE_DRAG_MIME = "application/x-jarvis-workspace-id";
const PROJECT_DRAG_MIME = "application/x-jarvis-project-id";
/**
 * What a 405 on a reorder means: this view already knows drag and drop, but
 * the backend serving it predates the endpoint — a restart brings the two
 * back in step. Shown instead of the server's bare "Method Not Allowed",
 * which names the HTTP verdict rather than the fix.
 */
const REORDER_NEEDS_RESTART = "This view is newer than the backend — restart the app and try again.";

/** A reorder failure in the user's terms: a stale backend gets the fix, anything else the server's own words. */
function reorderErrorMessage(error: unknown): string {
  if (error instanceof IdeApiError && error.status === 405) return REORDER_NEEDS_RESTART;
  if (error instanceof ChatLibraryError && error.status === 405) return REORDER_NEEDS_RESTART;
  return error instanceof Error ? error.message : String(error);
}

/** Why opening a folder failed, in the user's terms. */
function revealErrorMessage(error: unknown): string {
  if (error instanceof ChatLibraryError && error.message === "native-file-actions-disabled") {
    return "Opening folders works in the desktop app only.";
  }
  if (error instanceof ChatLibraryError && (error.status === 405 || (error.status === 404 && error.message === "Not Found"))) {
    return REORDER_NEEDS_RESTART;
  }
  return error instanceof Error ? error.message : String(error);
}

/**
 * A removal failure in the user's terms. FastAPI answers an unknown route with
 * a bare "Not Found" (the route's own 404 says which workspace is missing), so
 * that exact pair means a backend older than this view: restart it.
 */
function removalErrorMessage(error: unknown): string {
  if (error instanceof IdeApiError && (error.status === 405 || (error.status === 404 && error.message === "Not Found"))) {
    return REORDER_NEEDS_RESTART;
  }
  return error instanceof Error ? error.message : String(error);
}

/**
 * The project's only workspace when it carries the project's own name. Such a
 * project renders as ONE row: a folder header over a child with the same name
 * and the same count says everything twice.
 */
function soloWorkspace(project: IdeProject): ProjectWorkspace | null {
  if (project.workspaces.length !== 1) return null;
  const [workspace] = project.workspaces;
  return workspace.name.trim().toLowerCase() === project.name.trim().toLowerCase() ? workspace : null;
}

/** Running agents glow, an open but idle workspace is a solid dot, a saved (closed) one a hollow ring. */
function statusDotClass(workspace: ProjectWorkspace): string {
  if (workspace.status !== "open") return "border border-muted-foreground/45";
  return workspace.live_terminals > 0 ? "bg-emerald-500 ring-[3px] ring-emerald-500/15" : "bg-muted-foreground/40";
}

function readExpansion(): Record<string, boolean> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(EXPANSION_KEY) ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, open]) => typeof open === "boolean")) as Record<string, boolean>;
  } catch { return {}; /* Expansion is an optional preference; unavailable storage keeps defaults usable. */ }
}

function saveExpansion(value: Record<string, boolean>): void {
  try { localStorage.setItem(EXPANSION_KEY, JSON.stringify(value)); }
  catch { /* Browsers without storage keep the current in-memory choice. */ }
}

/** Compact, accessible navigation over real projects and their workspace IDs. */
export function IdeProjectTree() {
  const projects = useIdeProjectsStore((state) => state.projects);
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const pendingWorkspaceId = useIdeProjectsStore((state) => state.pendingWorkspaceId);
  const connectProject = useIdeProjectsStore((state) => state.connectProject);
  const newWorkspace = useIdeProjectsStore((state) => state.newWorkspace);
  const activateWorkspace = useIdeProjectsStore((state) => state.activateWorkspace);
  const openGitPanel = useIdeProjectsStore((state) => state.openGitPanel);
  const toggleVoice = useIdeProjectsStore((state) => state.toggleVoice);
  const requestRefresh = useIdeProjectsStore((state) => state.requestRefresh);
  const pushToast = useEventStore((state) => state.pushToast);
  const [expansion, setExpansion] = useState(readExpansion);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [mutatingId, setMutatingId] = useState<string | null>(null);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: string; before: boolean } | null>(null);
  const [draggedProjectId, setDraggedProjectId] = useState<string | null>(null);
  const [projectDropTarget, setProjectDropTarget] = useState<{ id: string; before: boolean } | null>(null);
  const [reordering, setReordering] = useState(false);
  const [contextMenu, setContextMenu] = useState<
    | { kind: "project"; projectId: string; x: number; y: number }
    | { kind: "workspace"; projectId: string; workspaceId: string; x: number; y: number }
    | null
  >(null);
  const [renamingWorkspaceId, setRenamingWorkspaceId] = useState<string | null>(null);
  const [draftWorkspaceName, setDraftWorkspaceName] = useState("");
  const [confirmWorkspace, setConfirmWorkspace] = useState<{ projectId: string; workspaceId: string } | null>(null);
  // What the open menu can offer beyond the row itself: the project's editors
  // and remote, and the panes of the workspace. Fetched when a menu opens, so
  // an item is only shown when it can actually run.
  const [launchers, setLaunchers] = useState<Record<string, ProjectLaunchers>>({});
  const [menuPanes, setMenuPanes] = useState<WorkspacePaneRow[] | null>(null);
  const [confirmProject, setConfirmProject] = useState<string | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const sawProjectSnapshot = useRef(false);
  const lastActiveId = useRef<string | null>(null);
  const lastPendingId = useRef<string | null>(null);
  const knownProjectIds = useRef<Set<string> | null>(null);
  const mutatingProjects = useRef<Set<string>>(new Set());
  const visible = projects.filter((project) => !project.archived && !project.scratch);
  const activeProject = visible.find((project) => project.workspaces.some((workspace) => workspace.id === activeWorkspaceId));

  const setProjectOpen = (id: string, open: boolean) => setExpansion((previous) => {
    const next = { ...previous, [id]: open };
    saveExpansion(next);
    return next;
  });

  useEffect(() => {
    if (!sawProjectSnapshot.current) {
      if (visible.length === 0) return;
      sawProjectSnapshot.current = true;
      lastActiveId.current = activeWorkspaceId;
      // Initial hydration respects a saved collapse, even for the active row.
      return;
    }
    if (lastActiveId.current !== activeWorkspaceId) {
      lastActiveId.current = activeWorkspaceId;
      if (activeProject) setProjectOpen(activeProject.id, true);
    }
  }, [activeWorkspaceId, activeProject?.id, projects]);

  useEffect(() => {
    if (pendingWorkspaceId === null) { lastPendingId.current = null; return; }
    if (lastPendingId.current === pendingWorkspaceId) return;
    const project = visible.find((entry) => entry.workspaces.some((workspace) => workspace.id === pendingWorkspaceId));
    if (project) { lastPendingId.current = pendingWorkspaceId; setProjectOpen(project.id, true); }
  }, [pendingWorkspaceId, projects]);

  useEffect(() => {
    const ids = new Set(visible.map((project) => project.id));
    if (knownProjectIds.current) {
      for (const id of ids) if (!knownProjectIds.current.has(id)) setProjectOpen(id, true);
    }
    if (ids.size > 0) knownProjectIds.current = ids;
  }, [projects]);

  const mutate = async (project: IdeProject, changes: { pinned?: boolean; name?: string }) => {
    if (mutatingProjects.current.has(project.id)) return;
    mutatingProjects.current.add(project.id);
    setMutatingId(project.id);
    try {
      try {
        await patchProject(project.id, changes);
      } catch (error) {
        if (!(error instanceof ChatLibraryError) || error.status !== 404) throw error;
        // Legacy workspace snapshots can appear in the graph before the chat
        // library has saved their derived project row. Register once, then
        // retry the same metadata patch without changing an existing name.
        await openProject(project.path);
        await patchProject(project.id, changes);
      }
      // A one-row project renames as one: its workspace follows, so the row
      // does not split into a header and a differently named child.
      const solo = soloWorkspace(project);
      if (changes.name && solo?.status === "open") await renameWorkspace(solo.id, changes.name);
      setRenamingId(null);
      requestRefresh();
    } catch (error) { pushToast("error", (error as Error).message); }
    finally { mutatingProjects.current.delete(project.id); setMutatingId(null); }
  };

  const moveWorkspace = async (sourceId: string, targetId: string, before: boolean) => {
    if (reordering || sourceId === targetId) return;
    const globalOrder = visible
      .flatMap((project) => project.workspaces)
      .filter((workspace) => workspace.status === "open")
      .map((workspace) => workspace.id);
    const sourceProject = visible.find((project) => project.workspaces.some((workspace) => workspace.id === sourceId));
    const targetProject = visible.find((project) => project.workspaces.some((workspace) => workspace.id === targetId));
    if (!sourceProject || !targetProject || sourceProject.id !== targetProject.id) return;
    const without = globalOrder.filter((id) => id !== sourceId);
    const targetIndex = without.indexOf(targetId);
    if (targetIndex < 0) return;
    const insertAt = before ? targetIndex : targetIndex + 1;
    const next = [...without.slice(0, insertAt), sourceId, ...without.slice(insertAt)];
    if (next.join("\u0000") === globalOrder.join("\u0000")) return;
    setReordering(true);
    try {
      await reorderWorkspaces(next);
      requestRefresh();
    } catch (error) { pushToast("error", reorderErrorMessage(error)); }
    finally { setReordering(false); setDraggedId(null); setDropTarget(null); }
  };

  const clearDragState = () => { setDraggedId(null); setDropTarget(null); };
  const clearProjectDragState = () => { setDraggedProjectId(null); setProjectDropTarget(null); };

  const openProjectMenu = (event: React.MouseEvent, projectId: string) => {
    // The app-wide Cut/Copy/Paste menu lives on document. Stop this click
    // here so a project row offers project actions instead of text editing.
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ kind: "project", projectId, x: event.clientX, y: event.clientY });
  };

  const openWorkspaceMenu = (event: React.MouseEvent, projectId: string, workspaceId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ kind: "workspace", projectId, workspaceId, x: event.clientX, y: event.clientY });
  };

  /** Open (or close again) the same menu as right-click, hanging under a ⋯ button. */
  const toggleAnchoredMenu = (
    event: React.MouseEvent<HTMLElement>,
    target: { kind: "project"; projectId: string } | { kind: "workspace"; projectId: string; workspaceId: string },
  ) => {
    event.stopPropagation();
    const same = contextMenu !== null && contextMenu.kind === target.kind && contextMenu.projectId === target.projectId
      && (target.kind === "project" || (contextMenu.kind === "workspace" && contextMenu.workspaceId === target.workspaceId));
    if (same) { setContextMenu(null); return; }
    const rect = event.currentTarget.getBoundingClientRect();
    setContextMenu({ ...target, x: rect.right - TREE_MENU_WIDTH, y: rect.bottom + 4 });
  };

  const copyPath = async (path: string) => {
    const copied = await robustCopy(path);
    pushToast(copied ? "success" : "error", copied ? "Folder path copied" : "Could not copy the folder path");
  };

  const revealFolder = async (projectId: string) => {
    try { await revealProject(projectId); }
    catch (error) { pushToast("error", revealErrorMessage(error)); }
  };

  const menuProjectId = contextMenu?.projectId ?? null;
  const menuWorkspaceId = contextMenu?.kind === "workspace" ? contextMenu.workspaceId : null;
  useEffect(() => {
    if (!menuProjectId) return;
    let live = true;
    fetchProjectLaunchers(menuProjectId)
      .then((found) => { if (live) setLaunchers((previous) => ({ ...previous, [menuProjectId]: found })); })
      // A headless or older backend has no launchers; the menu simply omits them.
      .catch(() => { if (live) setLaunchers((previous) => ({ ...previous, [menuProjectId]: { file_manager: false, editors: [], remote_url: null, remote_label: null } })); });
    return () => { live = false; };
  }, [menuProjectId]);

  useEffect(() => {
    setMenuPanes(null);
    if (!menuWorkspaceId) return;
    let live = true;
    fetchWorkspacePanes()
      .then((found) => { if (live) setMenuPanes(found.panes.filter((pane) => pane.workspace_id === menuWorkspaceId)); })
      .catch(() => { if (live) setMenuPanes([]); });
    return () => { live = false; };
  }, [menuWorkspaceId]);

  const openIn = async (projectId: string, target: string) => {
    try { await openProjectIn(projectId, target); }
    catch (error) { pushToast("error", revealErrorMessage(error)); }
  };

  /** One more pane of the workspace's own agent, then bring the workspace to the front. */
  const addAgent = async (workspace: ProjectWorkspace) => {
    try {
      await addTerminal({ workspace_id: workspace.id });
      if (workspace.id !== activeWorkspaceId) activateWorkspace(workspace.id);
      requestRefresh();
    } catch (error) { pushToast("error", (error as Error).message); }
  };

  /** A second workspace in the same folder with the same line-up of agents, freshly started. */
  const duplicateWorkspace = async (project: IdeProject, workspace: ProjectWorkspace, panes: WorkspacePaneRow[]) => {
    try {
      await startIdeSession(
        workspace.folder || project.path,
        panes.map((pane) => ({ agent: pane.agent, ...(pane.account ? { account: pane.account } : {}) })),
        { projectId: project.id, name: `${workspace.name} copy` },
      );
      requestRefresh();
    } catch (error) { pushToast("error", (error as Error).message); }
  };

  /** Escape to every agent that is busy — they stop the current task and keep running. */
  const interruptAgents = async (workspace: ProjectWorkspace, panes: WorkspacePaneRow[]) => {
    const results = await Promise.allSettled(panes.map((pane) => interruptTerminal(pane.key, workspace.id)));
    const failed = results.filter((result) => result.status === "rejected").length;
    if (failed) pushToast("error", `${failed} of ${panes.length} agents could not be interrupted.`);
    else pushToast("success", `Interrupted ${panes.length} ${panes.length === 1 ? "agent" : "agents"}.`);
  };

  // A whole workspace to a connected computer (or back): one folder transfer,
  // every pane's conversation carried, the agents then run there in tmux.
  const computers = useComputerChoices();
  const placeWorkspaceOn = async (workspace: ProjectWorkspace, computerId: string | null) => {
    const target = computerId ? (computers.find((c) => c.id === computerId)?.name ?? "the computer") : "this computer";
    pushToast("info", `Moving ${workspace.name} to ${target}. This can take a minute.`);
    try {
      const { message } = await placeWorkspace(workspace.id, computerId);
      window.dispatchEvent(new CustomEvent("jarvis:ide-panes-reconnect", { detail: { workspaceId: workspace.id } }));
      pushToast("success", `${workspace.name} now runs on ${target}. ${message}`.trim());
    } catch (error) { pushToast("error", (error as Error).message); }
  };

  const submitWorkspaceRename = async (workspace: ProjectWorkspace) => {
    const name = draftWorkspaceName.trim();
    if (!name || name === workspace.name) {
      setRenamingWorkspaceId(null);
      return;
    }
    if (mutatingId) return;
    setMutatingId(workspace.id);
    try {
      await renameWorkspace(workspace.id, name);
      setRenamingWorkspaceId(null);
      setContextMenu(null);
      requestRefresh();
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setMutatingId(null);
    }
  };

  const confirmCloseWorkspace = async () => {
    if (!confirmWorkspace || confirmBusy) return;
    const target = visible
      .flatMap((project) => project.workspaces.map((workspace) => ({ project, workspace })))
      .find((entry) => entry.workspace.id === confirmWorkspace.workspaceId);
    if (!target) {
      setConfirmWorkspace(null);
      return;
    }
    setConfirmBusy(true);
    try {
      await removeWorkspace(target.workspace.id);
      setConfirmWorkspace(null);
      setContextMenu(null);
      requestRefresh();
    } catch (error) {
      pushToast("error", removalErrorMessage(error));
    } finally {
      setConfirmBusy(false);
    }
  };

  const confirmDeleteProject = async () => {
    if (!confirmProject || confirmBusy) return;
    const target = visible.find((project) => project.id === confirmProject);
    if (!target) {
      setConfirmProject(null);
      return;
    }
    setConfirmBusy(true);
    try {
      // A project with workspaces would otherwise come straight back: the
      // sidebar derives a project row from every open AND remembered
      // workspace, so deleting the library entry alone changes nothing on
      // screen. Removing each one stops its agents and forgets its record.
      for (const workspace of target.workspaces) {
        await removeWorkspace(workspace.id);
      }
      await deleteProject(target.id);
      setConfirmProject(null);
      setContextMenu(null);
      requestRefresh();
    } catch (error) {
      pushToast("error", removalErrorMessage(error));
    } finally {
      setConfirmBusy(false);
    }
  };

  const moveProject = async (sourceId: string, targetId: string, before: boolean) => {
    if (reordering || sourceId === targetId) return;
    const pinned = visible.filter((project) => project.pinned);
    const unpinned = visible.filter((project) => !project.pinned);
    const section = pinned.some((project) => project.id === sourceId) ? pinned : unpinned;
    if (!section.some((project) => project.id === targetId)) return;
    const without = section.map((project) => project.id).filter((id) => id !== sourceId);
    const targetIndex = without.indexOf(targetId);
    if (targetIndex < 0) return;
    const insertAt = before ? targetIndex : targetIndex + 1;
    const sectionNext = [...without.slice(0, insertAt), sourceId, ...without.slice(insertAt)];
    if (sectionNext.join("\u0000") === section.map((project) => project.id).join("\u0000")) return;
    const orderOf = (ids: string[]) => {
      const rank = new Map(ids.map((id, index) => [id, index] as const));
      return (left: IdeProject, right: IdeProject) => (rank.get(left.id) ?? 0) - (rank.get(right.id) ?? 0);
    };
    const pinnedNext = (section === pinned ? sectionNext : pinned.map((project) => project.id));
    const unpinnedNext = (section === unpinned ? sectionNext : unpinned.map((project) => project.id));
    const next = [
      ...[...pinned].sort(orderOf(pinnedNext)).map((project) => project.id),
      ...[...unpinned].sort(orderOf(unpinnedNext)).map((project) => project.id),
    ];
    setReordering(true);
    try {
      await reorderProjects(next);
      requestRefresh();
    } catch (error) { pushToast("error", reorderErrorMessage(error)); }
    finally { setReordering(false); clearProjectDragState(); }
  };

  const projectRow = (project: IdeProject) => {
    const open = expansion[project.id] ?? (project.id === activeProject?.id || (!activeWorkspaceId && visible[0]?.id === project.id));
    const active = project.id === activeProject?.id;
    const working = mutatingId === project.id;
    const count = project.workspaces.reduce((total, workspace) => total + workspace.terminals, 0);
    const projectDraggable = renamingId !== project.id && !working && !reordering;
    const isProjectDragged = draggedProjectId === project.id;
    const isProjectDropBefore = projectDropTarget?.id === project.id && projectDropTarget.before;
    const isProjectDropAfter = projectDropTarget?.id === project.id && !projectDropTarget.before;
    const solo = soloWorkspace(project);
    const soloSelected = solo !== null && solo.id === activeWorkspaceId;
    const soloPending = solo !== null && solo.id === pendingWorkspaceId;
    const soloBlocked = solo !== null && solo.status === "closed" && !solo.restorable;
    const projectMenuOpen = contextMenu?.projectId === project.id
      && (solo ? contextMenu.kind === "workspace" && contextMenu.workspaceId === solo.id : contextMenu.kind === "project");
    return <div key={project.id} className="mb-px" data-testid={`ide-project-${project.id}`}>
      <div draggable={projectDraggable}
        data-testid={`ide-project-header-${project.id}`}
        onContextMenu={(event) => solo ? openWorkspaceMenu(event, project.id, solo.id) : openProjectMenu(event, project.id)}
        onDragStart={(event) => {
          if (!projectDraggable) { event.preventDefault(); return; }
          event.dataTransfer.setData(PROJECT_DRAG_MIME, project.id);
          event.dataTransfer.setData("text/plain", project.id);
          event.dataTransfer.effectAllowed = "move";
          setDraggedProjectId(project.id);
        }}
        onDragEnd={clearProjectDragState}
        onDragOver={(event) => {
          if (!draggedProjectId || draggedProjectId === project.id) return;
          if (event.dataTransfer.types.includes(WORKSPACE_DRAG_MIME)) return;
          if (!event.dataTransfer.types.includes(PROJECT_DRAG_MIME) && !event.dataTransfer.types.includes("text/plain")) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          const rect = event.currentTarget.getBoundingClientRect();
          const before = (event.clientY - rect.top) < rect.height / 2;
          setProjectDropTarget((current) => current?.id === project.id && current.before === before ? current : { id: project.id, before });
        }}
        onDragLeave={(event) => {
          const next = event.relatedTarget as Node | null;
          if (next && event.currentTarget.contains(next)) return;
          setProjectDropTarget((current) => current?.id === project.id ? null : current);
        }}
        onDrop={(event) => {
          if (!draggedProjectId) return;
          event.preventDefault();
          const sourceId = event.dataTransfer.getData(PROJECT_DRAG_MIME) || draggedProjectId;
          const rect = event.currentTarget.getBoundingClientRect();
          const before = (event.clientY - rect.top) < rect.height / 2;
          clearProjectDragState();
          void moveProject(sourceId, project.id, before);
        }}
        className={`group relative flex min-h-8 items-center rounded-md transition-colors hover:bg-muted ${active ? "text-foreground" : ""} ${soloSelected || soloPending ? "bg-muted" : ""} ${isProjectDragged ? "opacity-40" : ""} ${isProjectDropBefore ? "before:absolute before:-top-0.5 before:left-2 before:right-2 before:h-0.5 before:rounded-full before:bg-primary" : ""} ${isProjectDropAfter ? "after:absolute after:-bottom-0.5 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-primary" : ""} ${projectDraggable ? "cursor-grab active:cursor-grabbing" : ""}`}>
        {renamingId === project.id ? <form className="flex min-w-0 flex-1 items-center gap-1 px-2" onSubmit={(event) => { event.preventDefault(); const name = draftName.trim(); if (name && name !== project.name) void mutate(project, { name }); else setRenamingId(null); }}>
          <input autoFocus aria-label={`Rename ${project.name}`} value={draftName} maxLength={80} disabled={working}
            onChange={(event) => setDraftName(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setRenamingId(null); } }}
            className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          <button type="submit" aria-label={`Save ${project.name}`} disabled={working || !draftName.trim()} className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"><Check className="h-4 w-4" /></button>
          <button type="button" aria-label={`Cancel renaming ${project.name}`} onClick={() => setRenamingId(null)} className="rounded p-1 text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </form> : <>
          <button type="button" aria-label={solo ? `Open ${project.name}` : `${open ? "Collapse" : "Expand"} ${project.name}`}
            aria-expanded={solo ? undefined : open}
            aria-current={soloSelected ? "page" : undefined} aria-busy={soloPending || undefined}
            data-testid={solo ? `ide-workspace-${solo.id}` : undefined}
            disabled={soloBlocked}
            onClick={() => solo ? activateWorkspace(solo.id) : setProjectOpen(project.id, !open)}
            onKeyDown={(event) => {
              if (!event.altKey) return;
              if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
              event.preventDefault();
              const section = visible.filter((entry) => (entry.pinned || false) === (project.pinned || false));
              const index = section.findIndex((entry) => entry.id === project.id);
              const neighbour = event.key === "ArrowUp" ? section[index - 1] : section[index + 1];
              if (!neighbour) return;
              void moveProject(project.id, neighbour.id, event.key === "ArrowUp");
            }}
            title={soloBlocked ? "This workspace cannot be restored on this machine" : `${project.name} — drag to reorder, or press Alt plus arrow keys to move`}
            className={`flex min-h-8 min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 text-left text-sm font-medium [@media(hover:none)]:pr-14 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45 ${solo && solo.status !== "open" && !soloSelected && !soloPending ? "text-muted-foreground" : "text-foreground"}`}>
            {/* A one-row project has nothing to fold: its status dot takes the chevron's slot. */}
            {solo
              ? <span aria-hidden className="flex h-3 w-3 shrink-0 items-center justify-center">
                  {soloPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className={`h-1.5 w-1.5 rounded-full ${statusDotClass(solo)}`} />}
                </span>
              : open ? <ChevronDown aria-hidden className="h-3 w-3 shrink-0 text-muted-foreground/70" /> : <ChevronRight aria-hidden className="h-3 w-3 shrink-0 text-muted-foreground/70" />}
            <Folder aria-hidden className="ml-0.5 h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
            <span className="ml-0.5 min-w-0 flex-1 truncate">{project.name}</span>
            {soloPending && <span className="sr-only">Switching workspace</span>}
            {/* An open project's rows carry their own counts; the total only speaks for a folded one. */}
            {count > 0 && (solo || !open) && <span className="text-xs tabular-nums text-muted-foreground/80 transition-opacity group-hover:opacity-0 group-focus-within:opacity-0 [@media(hover:none)]:opacity-0" aria-label={`${count} agent ${count === 1 ? "session" : "sessions"}`}>{count}</span>}
          </button>
          <div className={`absolute inset-y-0 right-0 flex items-center rounded-r-md bg-gradient-to-l from-muted from-60% to-transparent pl-5 pr-1 transition-opacity ${projectMenuOpen ? "opacity-100" : "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 [@media(hover:none)]:pointer-events-auto [@media(hover:none)]:opacity-100"}`} data-project-menu={project.id}>
            <button type="button" aria-label={`Project actions for ${project.name}`} title="Project actions" aria-haspopup="menu" aria-expanded={projectMenuOpen}
              data-tree-menu-anchor
              onClick={(event) => toggleAnchoredMenu(event, solo
                ? { kind: "workspace", projectId: project.id, workspaceId: solo.id }
                : { kind: "project", projectId: project.id })}
              className={`rounded p-1 hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${projectMenuOpen ? "bg-background/70 text-foreground" : "text-muted-foreground"}`}>
              {working ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MoreHorizontal className="h-3.5 w-3.5" />}
            </button>
            <button type="button" aria-label={`New workspace in ${project.name}`} title="New workspace" onClick={() => newWorkspace(project.id)}
              className="rounded p-1 text-muted-foreground hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
        </>}
      </div>
      {open && !solo && <div className="mb-1 ml-3.5 flex flex-col gap-px border-l border-border/60 pl-1.5">
        {project.workspaces.map((workspace: ProjectWorkspace) => {
          const pending = workspace.id === pendingWorkspaceId;
          const selected = workspace.id === activeWorkspaceId;
          const draggable = workspace.status === "open" && !pending && !reordering;
          const isDragged = draggedId === workspace.id;
          const isDropBefore = dropTarget?.id === workspace.id && dropTarget.before;
          const isDropAfter = dropTarget?.id === workspace.id && !dropTarget.before;
          return <div key={workspace.id}
            data-testid={`ide-workspace-row-${workspace.id}`}
            draggable={draggable}
            onContextMenu={(event) => openWorkspaceMenu(event, project.id, workspace.id)}
            onDragStart={(event) => {
              if (!draggable) { event.preventDefault(); return; }
              event.dataTransfer.setData(WORKSPACE_DRAG_MIME, workspace.id);
              event.dataTransfer.setData("text/plain", workspace.id);
              event.dataTransfer.effectAllowed = "move";
              setDraggedId(workspace.id);
            }}
            onDragEnd={clearDragState}
            onDragOver={(event) => {
              if (!draggedId || draggedId === workspace.id || workspace.status !== "open") return;
              if (!event.dataTransfer.types.includes(WORKSPACE_DRAG_MIME) && !event.dataTransfer.types.includes("text/plain")) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              const rect = event.currentTarget.getBoundingClientRect();
              const before = (event.clientY - rect.top) < rect.height / 2;
              setDropTarget((current) => current?.id === workspace.id && current.before === before ? current : { id: workspace.id, before });
            }}
            onDragLeave={(event) => {
              const next = event.relatedTarget as Node | null;
              if (next && event.currentTarget.contains(next)) return;
              setDropTarget((current) => current?.id === workspace.id ? null : current);
            }}
            onDrop={(event) => {
              if (!draggedId) return;
              event.preventDefault();
              const sourceId = event.dataTransfer.getData(WORKSPACE_DRAG_MIME) || draggedId;
              const rect = event.currentTarget.getBoundingClientRect();
              const before = (event.clientY - rect.top) < rect.height / 2;
              clearDragState();
              void moveWorkspace(sourceId, workspace.id, before);
            }}
            className={`group/space relative flex min-h-8 items-center rounded-md transition-colors hover:bg-muted ${selected ? "bg-muted text-foreground" : ""} ${pending ? "bg-muted/80 text-foreground" : ""} ${isDragged ? "opacity-40" : ""} ${isDropBefore ? "before:absolute before:-top-0.5 before:left-2 before:right-2 before:h-0.5 before:rounded-full before:bg-primary" : ""} ${isDropAfter ? "after:absolute after:-bottom-0.5 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-primary" : ""}`}>
            {renamingWorkspaceId === workspace.id ? (
              <form
                className="flex min-w-0 flex-1 items-center gap-1 px-2 py-1"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitWorkspaceRename(workspace);
                }}
              >
                <input
                  autoFocus
                  aria-label={`Rename ${workspace.name}`}
                  value={draftWorkspaceName}
                  maxLength={80}
                  disabled={mutatingId === workspace.id}
                  onChange={(event) => setDraftWorkspaceName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.stopPropagation();
                      setRenamingWorkspaceId(null);
                    }
                  }}
                  className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
                />
                <button
                  type="submit"
                  aria-label={`Save ${workspace.name}`}
                  disabled={mutatingId === workspace.id || !draftWorkspaceName.trim()}
                  className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
                >
                  <Check className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  aria-label={`Cancel renaming ${workspace.name}`}
                  onClick={() => setRenamingWorkspaceId(null)}
                  className="rounded p-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </form>
            ) : (
              <>
                <button type="button" data-testid={`ide-workspace-${workspace.id}`}
            aria-current={selected ? "page" : undefined} aria-busy={pending || undefined}
            title={workspace.status === "closed" && !workspace.restorable ? "This workspace cannot be restored on this machine" : workspace.status === "open" ? `${workspace.name} — drag to reorder, or press Alt plus arrow keys to move` : undefined}
            disabled={workspace.status === "closed" && !workspace.restorable}
            onClick={() => activateWorkspace(workspace.id)}
            onKeyDown={(event) => {
              if (!event.altKey || workspace.status !== "open") return;
              if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
              event.preventDefault();
              const openInProject = project.workspaces.filter((entry) => entry.status === "open");
              const index = openInProject.findIndex((entry) => entry.id === workspace.id);
              const neighbour = event.key === "ArrowUp" ? openInProject[index - 1] : openInProject[index + 1];
              if (!neighbour) return;
              void moveWorkspace(workspace.id, neighbour.id, event.key === "ArrowUp");
            }}
            className={`flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45 ${selected || pending ? "text-foreground" : "text-muted-foreground"} ${draggable ? "cursor-grab active:cursor-grabbing" : ""}`}>
            {pending ? <Loader2 aria-hidden className="h-3 w-3 shrink-0 animate-spin" />
              : <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(workspace)}`} />}
            <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
            <span className="text-xs tabular-nums text-muted-foreground/80 transition-opacity group-hover/space:opacity-0 group-focus-within/space:opacity-0 [@media(hover:none)]:opacity-0">{workspace.terminals}</span>
            {pending && <span className="sr-only">Switching workspace</span>}
            </button>
            {(() => {
              const spaceMenuOpen = contextMenu?.kind === "workspace" && contextMenu.workspaceId === workspace.id;
              return <button type="button" aria-label={`Workspace actions for ${workspace.name}`} title="Workspace actions"
                aria-haspopup="menu" aria-expanded={spaceMenuOpen} data-tree-menu-anchor
                onClick={(event) => toggleAnchoredMenu(event, { kind: "workspace", projectId: project.id, workspaceId: workspace.id })}
                className={`absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-opacity hover:bg-background/70 hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/space:opacity-100 group-focus-within/space:opacity-100 [@media(hover:none)]:opacity-100 ${spaceMenuOpen ? "bg-background/70 text-foreground opacity-100" : "opacity-0"}`}>
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>;
            })()}
              </>
            )}
          </div>;
        })}
        {project.workspaces.length === 0 && <button type="button" onClick={() => newWorkspace(project.id)} className="px-2 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground">Create workspace</button>}
      </div>}
    </div>;
  };

  const run = (action: () => void) => () => { setContextMenu(null); action(); };

  /** Up to two installed editors and the hosted remote, as menu items. */
  const launcherItems = (project: IdeProject, scope: "project" | "workspace"): TreeMenuItem[] => {
    const found = launchers[project.id];
    if (!found) return [];
    const items: TreeMenuItem[] = found.editors.slice(0, 2).map((editor) => ({
      id: `editor-${editor.id}`, label: `Open in ${editor.label}`, icon: SquareCode, testId: `ide-${scope}-menu-editor-${editor.id}`,
      onSelect: run(() => void openIn(project.id, editor.id)),
    }));
    if (scope === "project" && found.file_manager) {
      items.push({ id: "reveal", label: FILE_MANAGER_LABEL, icon: FolderOpen, testId: "ide-project-menu-reveal", onSelect: run(() => void revealFolder(project.id)) });
    }
    if (scope === "project" && found.remote_url) {
      items.push({ id: "remote", label: `Open on ${found.remote_label ?? "the web"}`, icon: Globe, testId: "ide-project-menu-remote", onSelect: run(() => void openIn(project.id, "remote")) });
    }
    return items;
  };

  const projectMenuSections = (project: IdeProject): TreeMenuItem[][] => [
    [
      { id: "new", label: "New workspace", icon: FolderPlus, testId: "ide-project-menu-new", onSelect: run(() => newWorkspace(project.id)) },
      { id: "new-worktree", label: "New workspace in a worktree", icon: FolderGit2, testId: "ide-project-menu-new-worktree",
        hint: "Own folder and branch for its agents", onSelect: run(() => newWorkspace(project.id, { worktree: true })) },
      ...launcherItems(project, "project"),
    ],
    [
      { id: "rename", label: "Rename project", icon: Pencil, testId: "ide-project-menu-rename", onSelect: run(() => { setDraftName(project.name); setRenamingId(project.id); }) },
      { id: "pin", label: project.pinned ? "Unpin project" : "Pin project", icon: project.pinned ? PinOff : Pin, testId: "ide-project-menu-pin", onSelect: run(() => void mutate(project, { pinned: !project.pinned })) },
    ],
    [
      { id: "delete", label: "Delete project", icon: Trash2, testId: "ide-project-menu-delete", destructive: true, onSelect: run(() => setConfirmProject(project.id)) },
    ],
  ];

  const workspaceMenuSections = (project: IdeProject, workspace: ProjectWorkspace): TreeMenuItem[][] => {
    const isOpen = workspace.status === "open";
    const panes = menuPanes ?? [];
    const busy = panes.filter((pane) => pane.activity === "working" || pane.activity === "asking");
    const agentActions: TreeMenuItem[] = isOpen ? [
      { id: "add", label: "Add another agent", icon: Plus, testId: "ide-workspace-menu-add", onSelect: run(() => void addAgent(workspace)) },
      { id: "duplicate", label: "Duplicate workspace", icon: CopyPlus, testId: "ide-workspace-menu-duplicate", disabled: panes.length === 0,
        hint: panes.length ? `Starts ${panes.length} fresh ${panes.length === 1 ? "agent" : "agents"}` : undefined,
        onSelect: run(() => void duplicateWorkspace(project, workspace, panes)) },
      ...(busy.length ? [{ id: "interrupt", label: `Interrupt ${busy.length} working ${busy.length === 1 ? "agent" : "agents"}`, icon: OctagonPause,
        testId: "ide-workspace-menu-interrupt", hint: "Stops the current task, keeps the chat", onSelect: run(() => void interruptAgents(workspace, busy)) }] : []),
    ] : [
      { id: "reopen", label: "Reopen workspace", icon: ArrowUpRight, testId: "ide-workspace-menu-open", disabled: !workspace.restorable,
        hint: workspace.restorable ? undefined : "Its folder is not reachable",
        onSelect: run(() => { activateWorkspace(workspace.id); setProjectOpen(project.id, true); }) },
    ];
    return [
      agentActions,
      [
        ...launcherItems(project, "workspace"),
        { id: "copy", label: "Copy folder path", icon: Copy, testId: "ide-workspace-menu-copy", onSelect: run(() => void copyPath(workspace.folder || project.path)) },
      ],
      isOpen ? [
        ...computers.filter((computer) => computer.health.status !== "provisioning").map((computer) => ({
          id: `place-${computer.id}`, label: `Move workspace to ${computer.name}`, icon: Server,
          testId: `ide-workspace-menu-place-${computer.id}`, hint: "Keeps running while this PC is off",
          onSelect: run(() => void placeWorkspaceOn(workspace, computer.id)) })),
        ...(computers.length ? [{ id: "place-home", label: "Bring workspace back here", icon: ArrowUpRight,
          testId: "ide-workspace-menu-place-home", onSelect: run(() => void placeWorkspaceOn(workspace, null)) }] : []),
      ] : [],
      isOpen ? [
        { id: "git", label: "Git", icon: GitBranch, testId: "ide-workspace-menu-git", hint: "Commit, push, pull request, worktrees",
          onSelect: run(() => openGitPanel(workspace.id)) },
      ] : [],
      isOpen ? [
        { id: "rename", label: "Rename workspace", icon: Pencil, testId: "ide-workspace-menu-rename",
          onSelect: run(() => { setDraftWorkspaceName(workspace.name); setRenamingWorkspaceId(workspace.id); setProjectOpen(project.id, true); }) },
      ] : [],
      [
        { id: "remove", label: "Remove workspace", icon: Trash2, testId: "ide-workspace-menu-close", destructive: true,
          onSelect: run(() => setConfirmWorkspace({ projectId: project.id, workspaceId: workspace.id })) },
      ],
    ];
  };

  /**
   * A one-row project's menu: the workspace's agent actions plus the
   * project's own, each once — one rename, one launcher set, one removal.
   */
  const soloMenuSections = (project: IdeProject, workspace: ProjectWorkspace): TreeMenuItem[][] => {
    const [agentActions, , place = [], git = []] = workspaceMenuSections(project, workspace);
    const [projectCreate, projectMeta, projectDelete] = projectMenuSections(project);
    return [
      agentActions,
      [...projectCreate, { id: "copy", label: "Copy folder path", icon: Copy, testId: "ide-workspace-menu-copy", onSelect: run(() => void copyPath(workspace.folder || project.path)) }],
      place,
      git,
      projectMeta,
      projectDelete,
    ];
  };

  const menuProject = contextMenu ? visible.find((project) => project.id === contextMenu.projectId) ?? null : null;
  const menuWorkspace =
    contextMenu?.kind === "workspace" && menuProject
      ? (menuProject.workspaces.find((workspace) => workspace.id === contextMenu.workspaceId) ?? null)
      : null;
  const confirmWorkspaceTarget = confirmWorkspace
    ? visible
        .flatMap((project) => project.workspaces.map((workspace) => ({ project, workspace })))
        .find((entry) => entry.workspace.id === confirmWorkspace.workspaceId) ?? null
    : null;
  const confirmProjectTarget = confirmProject ? (visible.find((project) => project.id === confirmProject) ?? null) : null;

  return <div data-testid="ide-project-tree" className="flex-1 px-2 pb-3 pt-2">
    <div className="flex h-8 items-center justify-between pl-2 pr-1 text-sm font-semibold text-foreground">
      <span>Workspaces</span>
      <div className="flex items-center gap-0.5">
        <button type="button" aria-label="Jarvis Live" title="Jarvis Live" onClick={toggleVoice}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Mic className="h-3.5 w-3.5" /></button>
        <button type="button" aria-label="Connect project" title="Connect project folder" onClick={connectProject}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Plus className="h-3.5 w-3.5" /></button>
      </div>
    </div>
    {visible.some((project) => project.pinned) && <>
      <div className="px-2 pb-1 pt-3 text-xs font-medium text-muted-foreground/80">Pinned</div>
      {visible.filter((project) => project.pinned).map(projectRow)}
      {visible.some((project) => !project.pinned) && <div className="px-2 pb-1 pt-4 text-xs font-medium text-muted-foreground/80">Other projects</div>}
    </>}
    {visible.filter((project) => !project.pinned).map(projectRow)}
    {visible.length === 0 && <p className="px-2 py-2 text-xs text-muted-foreground">Connect a folder to start a project.</p>}
    {contextMenu && menuProject && (
      <TreeContextMenu
        x={contextMenu.x}
        y={contextMenu.y}
        onDismiss={() => setContextMenu(null)}
        label={contextMenu.kind === "workspace" && menuWorkspace ? menuWorkspace.name : menuProject.name}
        kind={contextMenu.kind}
        busy={mutatingId !== null || reordering || confirmBusy}
        sections={contextMenu.kind === "workspace" && menuWorkspace
          ? (soloWorkspace(menuProject)?.id === menuWorkspace.id
            ? soloMenuSections(menuProject, menuWorkspace)
            : workspaceMenuSections(menuProject, menuWorkspace))
          : projectMenuSections(menuProject)}
      />
    )}
    {confirmWorkspaceTarget && (
      <ConfirmTreeAction
        title={`Remove ${confirmWorkspaceTarget.workspace.name}?`}
        body={confirmWorkspaceTarget.workspace.status === "open"
          ? `Its ${confirmWorkspaceTarget.workspace.terminals} coding ${confirmWorkspaceTarget.workspace.terminals === 1 ? "agent" : "agents"} will stop and the workspace leaves the sidebar. The folder on disk and its chats stay untouched.`
          : "The workspace leaves the sidebar. The folder on disk and its chats stay untouched."}
        confirmLabel={confirmBusy ? "Removing…" : `Remove ${confirmWorkspaceTarget.workspace.name}`}
        busy={confirmBusy}
        testId="ide-workspace-confirm-close"
        onCancel={() => {
          if (!confirmBusy) setConfirmWorkspace(null);
        }}
        onConfirm={() => void confirmCloseWorkspace()}
      />
    )}
    {confirmProjectTarget && (
      <ConfirmTreeAction
        title={`Delete ${confirmProjectTarget.name}?`}
        body={(() => {
          const openSpaces = confirmProjectTarget.workspaces.filter((workspace) => workspace.status === "open");
          if (openSpaces.length === 0) {
            return `This forgets the project and its chats. The folders on disk stay untouched.`;
          }
          const agents = openSpaces.reduce((total, workspace) => total + workspace.terminals, 0);
          return `This stops ${agents} running ${agents === 1 ? "agent" : "agents"} in ${openSpaces.length} open ${openSpaces.length === 1 ? "workspace" : "workspaces"} and forgets the project and its chats. The folders on disk stay untouched.`;
        })()}
        confirmLabel={confirmBusy ? "Deleting…" : `Delete ${confirmProjectTarget.name}`}
        busy={confirmBusy}
        testId="ide-project-confirm-delete"
        onCancel={() => {
          if (!confirmBusy) setConfirmProject(null);
        }}
        onConfirm={() => void confirmDeleteProject()}
      />
    )}
  </div>;
}

const TREE_MENU_WIDTH = 240;

/** What the OS calls its file manager, for the "show the folder" item. */
const FILE_MANAGER_LABEL = (() => {
  const platform = typeof navigator === "undefined" ? "" : navigator.userAgent;
  if (/Windows/i.test(platform)) return "Show in Explorer";
  if (/Mac OS X|Macintosh/i.test(platform)) return "Show in Finder";
  return "Show in file manager";
})();
const TREE_MENU_MARGIN = 8;

/** One row of the sidebar's action menu. */
interface TreeMenuItem {
  id: string;
  label: string;
  icon: LucideIcon;
  testId: string;
  onSelect: () => void;
  disabled?: boolean;
  destructive?: boolean;
  /** A short second line: why an item is disabled, or what it keeps. */
  hint?: string;
}

/**
 * The one action menu for projects and workspaces, opened by right-click or
 * by a row's ⋯ button. Portalled to the body so no row's stacking context or
 * the sidebar's scroll clipping can cover it.
 */
function TreeContextMenu({
  x,
  y,
  onDismiss,
  label,
  kind,
  busy,
  sections,
}: {
  x: number;
  y: number;
  onDismiss: () => void;
  label: string;
  kind: "project" | "workspace";
  busy: boolean;
  sections: TreeMenuItem[][];
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onDismiss();
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) return;
      // A ⋯ button toggles the menu itself; dismissing here first would make
      // its click reopen the menu instead of closing it.
      if (target instanceof Element && target.closest("[data-tree-menu-anchor]")) return;
      onDismiss();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onDismiss);
    window.addEventListener("blur", onDismiss);
    document.addEventListener("scroll", onDismiss, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("blur", onDismiss);
      document.removeEventListener("scroll", onDismiss, true);
    };
  }, [onDismiss]);

  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node) return;
    const { width, height } = node.getBoundingClientRect();
    const maxX = window.innerWidth - width - TREE_MENU_MARGIN;
    const maxY = window.innerHeight - height - TREE_MENU_MARGIN;
    node.style.left = `${Math.max(TREE_MENU_MARGIN, Math.min(x, maxX))}px`;
    node.style.top = `${Math.max(TREE_MENU_MARGIN, Math.min(y, maxY))}px`;
    node.style.visibility = "visible";
    node.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [x, y]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [],
    );
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = (current + step + items.length) % items.length;
    items[next]?.focus();
  };

  const visibleSections = sections.filter((section) => section.length > 0);

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={`${kind === "workspace" ? "Workspace" : "Project"} actions for ${label}`}
      data-testid={kind === "workspace" ? "ide-workspace-menu" : "ide-project-menu"}
      onKeyDown={onMenuKeyDown}
      style={{ width: TREE_MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-float"
    >
      <div className="flex items-center gap-2 px-2.5 pb-1.5 pt-1 text-[11px] font-medium text-muted-foreground">
        {kind === "workspace" ? <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/60" /> : <Folder aria-hidden className="h-3 w-3 shrink-0" />}
        <span className="min-w-0 truncate">{label}</span>
      </div>
      {visibleSections.map((section, index) => (
        <div key={section[0]?.id ?? index} role="group" className={index > 0 ? "mt-1 border-t border-border/70 pt-1" : ""}>
          {section.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                disabled={busy || item.disabled}
                onClick={item.onSelect}
                data-testid={item.testId}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45 ${
                  item.destructive
                    ? "text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10"
                    : "text-foreground hover:bg-muted focus-visible:bg-muted"
                }`}
              >
                <Icon aria-hidden className={`h-4 w-4 shrink-0 ${item.destructive ? "" : "text-muted-foreground"}`} />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate">{item.label}</span>
                  {item.hint && <span className="truncate text-[11px] text-muted-foreground">{item.hint}</span>}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>,
    document.body,
  );
}

function ConfirmTreeAction({
  title,
  body,
  confirmLabel,
  busy,
  testId,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  busy: boolean;
  testId: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid={testId}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-popover shadow-float p-5">
        <h3 className="font-display text-base font-semibold text-popover-foreground">{title}</h3>
        <p className="mt-2 text-sm text-muted-foreground">{body}</p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            className="rounded-lg bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground transition-colors hover:bg-secondary/80 disabled:opacity-50"
            autoFocus
            disabled={busy}
            onClick={onCancel}
          >
            Keep
          </button>
          <button
            type="button"
            data-testid={`${testId}-confirm`}
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground shadow transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
