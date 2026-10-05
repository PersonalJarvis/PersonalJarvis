/**
 * A project's actions: named shell commands ("Dev server" → `npm run dev`)
 * the thread header runs in the terminal drawer with one click.
 *
 * Kept per project in this browser's storage — a convenience a person sets up
 * once; storage that is blocked or cleared leaves the header with its plain
 * "Add action" button and nothing breaks.
 */

export interface ProjectAction {
  id: string;
  name: string;
  command: string;
}

const KEY = "jarvis.ide.projectActions.v1";

function readAll(): Record<string, ProjectAction[]> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    const out: Record<string, ProjectAction[]> = {};
    for (const [project, list] of Object.entries(raw)) {
      if (!Array.isArray(list)) continue;
      out[project] = list.filter((row): row is ProjectAction =>
        Boolean(row) && typeof row.id === "string" && typeof row.name === "string" && typeof row.command === "string");
    }
    return out;
  } catch {
    return {};
  }
}

function writeAll(all: Record<string, ProjectAction[]>): void {
  try { localStorage.setItem(KEY, JSON.stringify(all)); }
  catch { /* storage blocked: the actions last for this session only */ }
}

export function readProjectActions(projectId: string): ProjectAction[] {
  return readAll()[projectId] ?? [];
}

/** Add or replace (same id) one action; returns the project's new list. */
export function saveProjectAction(projectId: string, action: ProjectAction): ProjectAction[] {
  const all = readAll();
  const list = all[projectId] ?? [];
  const at = list.findIndex((row) => row.id === action.id);
  const next = at >= 0 ? list.map((row, i) => (i === at ? action : row)) : [...list, action];
  writeAll({ ...all, [projectId]: next });
  return next;
}

export function deleteProjectAction(projectId: string, actionId: string): ProjectAction[] {
  const all = readAll();
  const next = (all[projectId] ?? []).filter((row) => row.id !== actionId);
  writeAll({ ...all, [projectId]: next });
  return next;
}

/** What the shell is sent to run `command`: the line plus Enter. */
export function actionInput(command: string): string {
  return `${command.replace(/\r?\n/g, " ").trim()}\r`;
}
