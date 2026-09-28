import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Folder, Loader2, Mic, MoreHorizontal, Pin, Plus, X } from "lucide-react";
import { ChatLibraryError, openProject, patchProject } from "@/lib/chatLibraryApi";
import { reorderWorkspaces, type IdeProject, type ProjectWorkspace } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";

const EXPANSION_KEY = "jarvis.ide.projectExpansion.v1";
const WORKSPACE_DRAG_MIME = "application/x-jarvis-workspace-id";

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
  const [reordering, setReordering] = useState(false);
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
    } catch (error) { pushToast("error", (error as Error).message); }
    finally { setReordering(false); setDraggedId(null); setDropTarget(null); }
  };

  const clearDragState = () => { setDraggedId(null); setDropTarget(null); };

  const projectRow = (project: IdeProject) => {
    const open = expansion[project.id] ?? (project.id === activeProject?.id || (!activeWorkspaceId && visible[0]?.id === project.id));
    const active = project.id === activeProject?.id;
    const working = mutatingId === project.id;
    const count = project.workspaces.reduce((total, workspace) => total + workspace.terminals, 0);
    return <div key={project.id} className="mb-0.5" data-testid={`ide-project-${project.id}`}>
      <div className={`group relative flex min-h-11 items-center rounded-xl border border-transparent transition-colors hover:bg-muted/70 ${active ? "border-border/50 bg-muted/55" : ""}`}>
        {renamingId === project.id ? <form className="flex min-w-0 flex-1 items-center gap-1 px-2" onSubmit={(event) => { event.preventDefault(); const name = draftName.trim(); if (name && name !== project.name) void mutate(project, { name }); else setRenamingId(null); }}>
          <input autoFocus aria-label={`Rename ${project.name}`} value={draftName} maxLength={80} disabled={working}
            onChange={(event) => setDraftName(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setRenamingId(null); } }}
            className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          <button type="submit" aria-label={`Save ${project.name}`} disabled={working || !draftName.trim()} className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"><Check className="h-4 w-4" /></button>
          <button type="button" aria-label={`Cancel renaming ${project.name}`} onClick={() => setRenamingId(null)} className="rounded p-1 text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </form> : <>
          <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${project.name}`} aria-expanded={open}
            onClick={() => setProjectOpen(project.id, !open)}
            title={project.name}
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
          </div>;
        })}
        {project.workspaces.length === 0 && <button type="button" onClick={() => newWorkspace(project.id)} className="px-2 py-2 text-left text-sm text-muted-foreground hover:text-foreground">Create workspace</button>}
      </div>}
    </div>;
  };

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
  </div>;
}
