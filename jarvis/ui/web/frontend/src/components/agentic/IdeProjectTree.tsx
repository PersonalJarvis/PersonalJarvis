import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Folder, Plus } from "lucide-react";
import { useIdeProjectsStore } from "@/store/ideProjects";

/** The IDE's compact project navigation. Workspace IDs, never paths, select runtime context. */
export function IdeProjectTree() {
  const projects = useIdeProjectsStore((state) => state.projects);
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const connectProject = useIdeProjectsStore((state) => state.connectProject);
  const newWorkspace = useIdeProjectsStore((state) => state.newWorkspace);
  const activateWorkspace = useIdeProjectsStore((state) => state.activateWorkspace);
  const [expanded, setExpanded] = useState<string[]>([]);
  const lastAutoExpandedWorkspace = useRef<string | null>(null);

  useEffect(() => {
    const current = projects.find((project) => project.workspaces.some((workspace) => workspace.id === activeWorkspaceId));
    if (current && activeWorkspaceId !== lastAutoExpandedWorkspace.current) {
      lastAutoExpandedWorkspace.current = activeWorkspaceId;
      setExpanded((old) => old.includes(current.id) ? old : [...old, current.id]);
    }
  }, [activeWorkspaceId, projects]);

  return <div data-testid="ide-project-tree" className="mt-1 px-2 pb-2">
    <div className="group flex items-center justify-between px-2 pb-1 text-xs font-medium text-muted-foreground">
      <span>Projects</span>
      <button type="button" aria-label="Connect project" title="Connect project folder" onClick={connectProject}
        className="rounded p-1 hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
    {projects.filter((project) => !project.archived && !project.scratch).map((project) => {
      const open = expanded.includes(project.id);
      const active = project.workspaces.some((workspace) => workspace.id === activeWorkspaceId);
      return <div key={project.id} className="mb-0.5">
        <div className={`group flex min-h-9 items-center rounded-lg transition-colors hover:bg-muted/70 ${active ? "bg-muted" : ""}`}>
          <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${project.name}`} aria-expanded={open}
            onClick={() => setExpanded((old) => open ? old.filter((id) => id !== project.id) : [...old, project.id])}
            className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
            <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate">{project.name}</span>
          </button>
          <button type="button" aria-label={`New workspace in ${project.name}`} title="New workspace" onClick={() => newWorkspace(project.id)}
            className="mr-1 rounded p-1.5 text-muted-foreground opacity-70 hover:bg-background hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        {open && <div className="ml-5 border-l border-border/70 pl-2">
          {project.workspaces.map((workspace) => <button key={workspace.id} type="button"
            data-testid={`ide-workspace-${workspace.id}`}
            aria-current={workspace.id === activeWorkspaceId ? "page" : undefined}
            title={workspace.status === "closed" && !workspace.restorable ? "This workspace cannot be restored on this machine" : undefined}
            disabled={workspace.status === "closed" && !workspace.restorable}
            onClick={() => activateWorkspace(workspace.id)}
            className={`flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45 ${workspace.id === activeWorkspaceId ? "bg-muted text-foreground" : "text-muted-foreground"}`}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${workspace.status === "open" && workspace.live_terminals > 0 ? "bg-emerald-500" : "bg-muted-foreground/40"}`} />
            <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
            <span className="tabular-nums opacity-70">{workspace.terminals}</span>
          </button>)}
          {project.workspaces.length === 0 && <button type="button" onClick={() => newWorkspace(project.id)} className="px-2 py-2 text-left text-xs text-muted-foreground hover:text-foreground">Create workspace</button>}
        </div>}
      </div>;
    })}
    {projects.every((project) => project.archived || project.scratch) && <p className="px-2 py-2 text-xs text-muted-foreground">Connect a folder to start a project.</p>}
  </div>;
}
