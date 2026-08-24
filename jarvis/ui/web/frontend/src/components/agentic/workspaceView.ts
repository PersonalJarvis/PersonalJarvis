/**
 * How a workspace is read — grid or chat — and where that answer is kept.
 *
 * Its own module rather than a corner of `AgenticGrid.tsx`, because the answer
 * is no longer the grid's to give. Since 2026-08-24 the two modes are two
 * different SURFACES: `grid` is the terminal wall, `chat` is the agent chat
 * (the same one the front page runs) pointed at the workspace's folder. The
 * view that owns both — `views/AgenticIdeView` — holds the choice, the
 * workspace bar draws the switch, the sidebar changes face with it, and the
 * grid is only one of the things that reads it. A module every one of them can
 * import without pulling a 3000-line grid behind it.
 *
 * Layer 4 of the enum contract; `jarvis/agentic_ide/workspace_view.py` is the
 * source of truth and the backend asserts the two agree at import.
 */
export type WorkspaceView = "grid" | "chat";

/** Every value, in the order the switch and the wizard offer them. */
export const WORKSPACE_VIEWS: readonly WorkspaceView[] = ["grid", "chat"];

export function isWorkspaceView(raw: string): raw is WorkspaceView {
  return (WORKSPACE_VIEWS as readonly string[]).includes(raw);
}

const VIEW_KEY = "jarvis.agenticIde.workspaceView";

export function storedViewMode(): WorkspaceView | null {
  return readStored(VIEW_KEY, (raw) => (isWorkspaceView(raw) ? raw : null));
}

/**
 * Record which way the workspace should be read, ahead of anything mounting.
 *
 * Used by the workspace wizard (its last step asks grid-or-chat before a
 * workspace opens) and by the switch in the bar, so the two can never disagree
 * about where the answer lives.
 */
export function rememberViewMode(next: WorkspaceView): void {
  writeStored(VIEW_KEY, next);
}

function readStored<T>(key: string, parse: (raw: string) => T | null): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? null : parse(raw);
  } catch {
    // Private mode / storage disabled — fall back to the defaults rather than
    // taking the whole workspace down over a preference.
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* nothing to do — the preference just will not survive this session */
  }
}
