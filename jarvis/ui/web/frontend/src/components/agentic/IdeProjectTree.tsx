import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight, Folder, FolderPlus, Loader2, Mic, MoreHorizontal, Pencil, Pin, Plus, Trash2, X } from "lucide-react";
import { ChatLibraryError, deleteProject, openProject, patchProject, reorderProjects } from "@/lib/chatLibraryApi";
import { closeWorkspace, removeWorkspace, renameWorkspace, IdeApiError, reorderWorkspaces, type IdeProject, type ProjectWorkspace } from "@/lib/agenticIdeApi";
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
  const openWorkspaceOptions = useIdeProjectsStore((state) => state.openWorkspaceOptions);
  const toggleVoice = useIdeProjectsStore((state) => state.toggleVoice);
  const requestRefresh = useIdeProjectsStore((state) => state.requestRefresh);
  const pushToast = useEventStore((state) => state.pushToast);
  const [expansion, setExpansion] = useState(readExpansion);
  const [menuId, setMenuId] = useState<string | null>(null);
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

  useEffect(() => {
    if (!menuId) return;
    const dismiss = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || event.target.closest("[data-project-menu]")?.getAttribute("data-project-menu") !== menuId) setMenuId(null);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuId(null); };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", onKey); };
  }, [menuId]);

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
      setMenuId(null);
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
    setMenuId(null);
    setContextMenu({ kind: "project", projectId, x: event.clientX, y: event.clientY });
  };

  const openWorkspaceMenu = (event: React.MouseEvent, projectId: string, workspaceId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setMenuId(null);
    setContextMenu({ kind: "workspace", projectId, workspaceId, x: event.clientX, y: event.clientY });
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
      pushToast("error", (error as Error).message);
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
      // A project with open workspaces would otherwise come straight back:
      // the sidebar derives a project row from every running workspace, so
      // deleting the library entry alone changes nothing on screen. The
      // confirm dialog says so, and confirming stops the agents first.
      for (const workspace of target.workspaces.filter((entry) => entry.status === "open")) {
        await closeWorkspace(workspace.id);
      }
      await deleteProject(target.id);
      setConfirmProject(null);
      setContextMenu(null);
      setMenuId(null);
      requestRefresh();
    } catch (error) {
      pushToast("error", (error as Error).message);
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
    return <div key={project.id} className="mb-0.5" data-testid={`ide-project-${project.id}`}>
      <div draggable={projectDraggable}
        data-testid={`ide-project-header-${project.id}`}
        onContextMenu={(event) => openProjectMenu(event, project.id)}
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
        className={`group relative flex min-h-11 items-center rounded-xl border border-transparent transition-colors hover:bg-muted/70 ${active ? "border-border/50 bg-muted/55" : ""} ${isProjectDragged ? "opacity-40" : ""} ${isProjectDropBefore ? "before:absolute before:-top-0.5 before:left-2 before:right-2 before:h-0.5 before:rounded-full before:bg-primary" : ""} ${isProjectDropAfter ? "after:absolute after:-bottom-0.5 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-primary" : ""} ${projectDraggable ? "cursor-grab active:cursor-grabbing" : ""}`}>
        {renamingId === project.id ? <form className="flex min-w-0 flex-1 items-center gap-1 px-2" onSubmit={(event) => { event.preventDefault(); const name = draftName.trim(); if (name && name !== project.name) void mutate(project, { name }); else setRenamingId(null); }}>
          <input autoFocus aria-label={`Rename ${project.name}`} value={draftName} maxLength={80} disabled={working}
            onChange={(event) => setDraftName(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setRenamingId(null); } }}
            className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          <button type="submit" aria-label={`Save ${project.name}`} disabled={working || !draftName.trim()} className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"><Check className="h-4 w-4" /></button>
          <button type="button" aria-label={`Cancel renaming ${project.name}`} onClick={() => setRenamingId(null)} className="rounded p-1 text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </form> : <>
          <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${project.name}`} aria-expanded={open}
            onClick={() => setProjectOpen(project.id, !open)}
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
            title={`${project.name} — drag to reorder, or press Alt plus arrow keys to move`}
            className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2.5 text-left text-[16px] font-medium text-foreground group-hover:pr-16 group-focus-within:pr-16 [@media(hover:none)]:pr-16 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
            <Folder className="h-[18px] w-[18px] shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate">{project.name}</span>
            {count > 0 && <span className="rounded-md bg-background/50 px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground" aria-label={`${count} agent ${count === 1 ? "session" : "sessions"}`}>{count}</span>}
          </button>
          <div className={`absolute right-1 top-1/2 flex -translate-y-1/2 items-center transition-opacity ${menuId === project.id ? "opacity-100" : "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 [@media(hover:none)]:pointer-events-auto [@media(hover:none)]:opacity-100"}`} data-project-menu={project.id}>
            <button type="button" aria-label={`Project actions for ${project.name}`} title="Project actions" aria-expanded={menuId === project.id}
              onClick={() => setMenuId((current) => current === project.id ? null : project.id)}
              className="rounded p-1.5 text-muted-foreground/70 hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {working ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MoreHorizontal className="h-3.5 w-3.5" />}
            </button>
            <button type="button" aria-label={`New workspace in ${project.name}`} title="New workspace" onClick={() => newWorkspace(project.id)}
              className="rounded p-1.5 text-muted-foreground/70 hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Plus className="h-3.5 w-3.5" />
            </button>
            {menuId === project.id && <div className="absolute right-0 top-full z-30 mt-1 min-w-40 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg">
              <button type="button" disabled={working} onClick={() => void mutate(project, { pinned: !project.pinned })}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted disabled:opacity-50"><Pin className="h-3.5 w-3.5" />{project.pinned ? "Unpin project" : "Pin project"}</button>
              <button type="button" disabled={working} onClick={() => { setMenuId(null); setDraftName(project.name); setRenamingId(project.id); }}
                className="block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted disabled:opacity-50">Rename project</button>
            </div>}
          </div>
        </>}
      </div>
      {open && <div className="ml-5 border-l border-border/70 pl-2 pt-0.5">
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
            className={`group/space relative flex min-h-10 items-center rounded-xl border border-transparent transition-colors hover:bg-muted/70 ${selected ? "border-border/50 bg-muted text-foreground" : ""} ${pending ? "bg-muted/80 text-foreground" : ""} ${isDragged ? "opacity-40" : ""} ${isDropBefore ? "before:absolute before:-top-0.5 before:left-2 before:right-2 before:h-0.5 before:rounded-full before:bg-primary" : ""} ${isDropAfter ? "after:absolute after:-bottom-0.5 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-primary" : ""}`}>
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
            className={`flex min-h-10 min-w-0 flex-1 items-center gap-2.5 rounded-xl px-2.5 text-left text-[15px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45 ${selected || pending ? "text-foreground" : "text-muted-foreground"} ${draggable ? "cursor-grab active:cursor-grabbing" : ""}`}>
            {pending ? <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin" />
              : <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${workspace.status === "open" && workspace.live_terminals > 0 ? "bg-emerald-500" : "bg-muted-foreground/40"}`} />}
            <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
            <span className="text-xs tabular-nums opacity-70">{workspace.terminals}</span>
            {pending && <span className="sr-only">Switching workspace</span>}
            </button>
            {selected && <button type="button" aria-label={`Workspace options for ${workspace.name}`} title="Workspace options"
              onClick={() => openWorkspaceOptions(workspace.id)}
              className="mr-1 rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-background/70 hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/space:opacity-100 group-focus-within/space:opacity-100 [@media(hover:none)]:opacity-100">
              <MoreHorizontal className="h-4 w-4" />
            </button>}
              </>
            )}
          </div>;
        })}
        {project.workspaces.length === 0 && <button type="button" onClick={() => newWorkspace(project.id)} className="px-2 py-2 text-left text-sm text-muted-foreground hover:text-foreground">Create workspace</button>}
      </div>}
    </div>;
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

  return <div data-testid="ide-project-tree" className="mx-2 mt-2 flex-1 rounded-2xl border border-border/50 bg-card/40 p-2 pb-3">
    <div className="flex items-center justify-between px-2 pb-2 pt-1 text-[17px] font-semibold text-foreground">
      <span>Workspaces</span>
      <div className="flex items-center gap-0.5">
        <button type="button" aria-label="Jarvis Live" title="Jarvis Live" onClick={toggleVoice}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Mic className="h-4 w-4" /></button>
        <button type="button" aria-label="Connect project" title="Connect project folder" onClick={connectProject}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Plus className="h-4 w-4" /></button>
      </div>
    </div>
    {visible.some((project) => project.pinned) && <>
      <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-muted-foreground">Pinned</div>
      {visible.filter((project) => project.pinned).map(projectRow)}
      {visible.some((project) => !project.pinned) && <div className="px-2 pb-1 pt-3 text-[11px] font-medium text-muted-foreground">Other projects</div>}
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
        isOpen={contextMenu.kind === "workspace" ? menuWorkspace?.status === "open" : undefined}
        isActive={contextMenu.kind === "workspace" ? menuWorkspace?.id === activeWorkspaceId : menuProject.id === activeProject?.id}
        isExpanded={contextMenu.kind === "project" ? (expansion[menuProject.id] ?? menuProject.id === activeProject?.id) : undefined}
        isPinned={menuProject.pinned}
        busy={mutatingId !== null || reordering || confirmBusy}
        onOpen={contextMenu.kind === "workspace" && menuWorkspace ? () => {
          const id = menuWorkspace.id;
          setContextMenu(null);
          activateWorkspace(id);
          setProjectOpen(menuProject.id, true);
        } : undefined}
        onToggleExpand={contextMenu.kind === "project" ? () => {
          setProjectOpen(menuProject.id, !(expansion[menuProject.id] ?? menuProject.id === activeProject?.id));
          setContextMenu(null);
        } : undefined}
        onNewWorkspace={() => {
          setContextMenu(null);
          newWorkspace(menuProject.id);
        }}
        onPin={() => {
          const target = menuProject;
          setContextMenu(null);
          void mutate(target, { pinned: !target.pinned });
        }}
        onRenameProject={contextMenu.kind === "project" ? () => {
          setContextMenu(null);
          setMenuId(null);
          setDraftName(menuProject.name);
          setRenamingId(menuProject.id);
          setProjectOpen(menuProject.id, true);
        } : undefined}
        onRenameWorkspace={contextMenu.kind === "workspace" && menuWorkspace ? () => {
          setContextMenu(null);
          setDraftWorkspaceName(menuWorkspace.name);
          setRenamingWorkspaceId(menuWorkspace.id);
          setProjectOpen(menuProject.id, true);
        } : undefined}
        onWorkspaceOptions={contextMenu.kind === "workspace" && menuWorkspace ? () => {
          const id = menuWorkspace.id;
          setContextMenu(null);
          openWorkspaceOptions(id);
        } : undefined}
        onCloseWorkspace={contextMenu.kind === "workspace" && menuWorkspace ? () => {
          setContextMenu(null);
          setConfirmWorkspace({ projectId: menuProject.id, workspaceId: menuWorkspace.id });
        } : undefined}
        onDeleteProject={contextMenu.kind === "project" ? () => {
          setContextMenu(null);
          setConfirmProject(menuProject.id);
        } : undefined}
      />
    )}
    {confirmWorkspaceTarget && (
      <ConfirmTreeAction
        title={`Remove ${confirmWorkspaceTarget.workspace.name}?`}
        body={
          confirmWorkspaceTarget.workspace.status === "open"
            ? `Its ${confirmWorkspaceTarget.workspace.terminals} coding ${confirmWorkspaceTarget.workspace.terminals === 1 ? "agent" : "agents"} will stop and the workspace leaves the sidebar. The folder on disk and its chats stay untouched.`
            : "The workspace leaves the sidebar. The folder on disk and its chats stay untouched."
        }
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

const TREE_MENU_WIDTH = 230;
const TREE_MENU_MARGIN = 8;

function TreeContextMenu({
  x,
  y,
  onDismiss,
  label,
  kind,
  isOpen,
  isActive,
  isExpanded,
  isPinned,
  busy,
  onOpen,
  onToggleExpand,
  onNewWorkspace,
  onPin,
  onRenameProject,
  onRenameWorkspace,
  onWorkspaceOptions,
  onCloseWorkspace,
  onDeleteProject,
}: {
  x: number;
  y: number;
  onDismiss: () => void;
  label: string;
  kind: "project" | "workspace";
  isOpen?: boolean;
  isActive?: boolean;
  isExpanded?: boolean;
  isPinned?: boolean;
  busy: boolean;
  onOpen?: () => void;
  onToggleExpand?: () => void;
  onNewWorkspace: () => void;
  onPin: () => void;
  onRenameProject?: () => void;
  onRenameWorkspace?: () => void;
  onWorkspaceOptions?: () => void;
  onCloseWorkspace?: () => void;
  onDeleteProject?: () => void;
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
      if (!menuRef.current?.contains(event.target as Node)) onDismiss();
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

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={`${kind === "workspace" ? "Workspace" : "Project"} actions for ${label}`}
      data-testid={kind === "workspace" ? "ide-workspace-menu" : "ide-project-menu"}
      onKeyDown={onMenuKeyDown}
      style={{ width: TREE_MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-[100] overflow-hidden rounded-md border border-border bg-background py-1 shadow-lg"
    >
      {kind === "workspace" && onOpen && (
        <button
          type="button"
          role="menuitem"
          disabled={busy || isActive}
          onClick={onOpen}
          data-testid="ide-workspace-menu-open"
          className="block w-full px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          {isActive ? "Current workspace" : "Open workspace"}
        </button>
      )}
      {kind === "project" && onToggleExpand && (
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={onToggleExpand}
          className="block w-full px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          {isExpanded ? "Collapse project" : "Expand project"}
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        disabled={busy}
        onClick={onNewWorkspace}
        data-testid={kind === "workspace" ? "ide-workspace-menu-new" : "ide-project-menu-new"}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
      >
        <FolderPlus aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        New workspace
      </button>
      <button
        type="button"
        role="menuitem"
        disabled={busy}
        onClick={onPin}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
      >
        <Pin aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        {isPinned ? "Unpin project" : "Pin project"}
      </button>
      {(onRenameWorkspace ?? onRenameProject) && (
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={onRenameWorkspace ?? onRenameProject}
          data-testid={kind === "workspace" ? "ide-workspace-menu-rename" : "ide-project-menu-rename"}
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          <Pencil aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          {kind === "workspace" ? "Rename workspace" : "Rename project"}
        </button>
      )}
      {kind === "workspace" && onWorkspaceOptions && isOpen && (
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={onWorkspaceOptions}
          data-testid="ide-workspace-menu-options"
          className="block w-full px-3 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          Workspace options…
        </button>
      )}
      {kind === "workspace" && onCloseWorkspace && (
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={onCloseWorkspace}
          data-testid="ide-workspace-menu-close"
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
        >
          <Trash2 aria-hidden className="h-3.5 w-3.5 shrink-0" />
          Remove workspace
        </button>
      )}
      {kind === "project" && onDeleteProject && (
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={onDeleteProject}
          data-testid="ide-project-menu-delete"
          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
        >
          <Trash2 aria-hidden className="h-3.5 w-3.5 shrink-0" />
          Delete project
        </button>
      )}
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
