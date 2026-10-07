import { useCodeEditorStore, fileKeyOf, tabsOf } from "@/store/codeEditor";
import { fetchEditorState, putEditorTabs } from "./editorApi";
import { flushBackups, queueRestore } from "./editorModels";

/**
 * Hot exit: the editor's open tabs and unsaved text come back after the app
 * closes or restarts (backend: jarvis/agentic_ide/editor_backups.py).
 *
 * Tabs are written shortly after they change; unsaved text is backed up by
 * editorModels after each pause in typing; anything still waiting is sent with
 * `keepalive` when the window goes away.
 */
const TABS_DELAY_MS = 800;
const restored = new Set<string>();
const restoring = new Set<string>();
const tabTimers = new Map<string, number>();
const lastSent = new Map<string, string>();

function snapshot(workspaceId: string) {
  const state = useCodeEditorStore.getState();
  const tabs = tabsOf(state, workspaceId).map(({ path, mode, preview }) => ({ path, mode, preview }));
  const active = tabsOf(state, workspaceId).find((tab) => tab.key === state.active[workspaceId])?.path ?? null;
  return { tabs, active, key: JSON.stringify([tabs, active]) };
}

function sendTabs(workspaceId: string, keepalive = false): void {
  tabTimers.delete(workspaceId);
  const { tabs, active, key } = snapshot(workspaceId);
  if (lastSent.get(workspaceId) === key) return;
  lastSent.set(workspaceId, key);
  void putEditorTabs(workspaceId, tabs, active, keepalive).catch((error: unknown) => {
    lastSent.delete(workspaceId);
    console.warn("Editor tabs could not be remembered:", (error as Error).message);
  });
}

// Only workspaces whose last state has been read are written back, so an empty
// editor at start-up never overwrites the tabs it is about to restore.
useCodeEditorStore.subscribe((state, previous) => {
  if (state.tabs === previous.tabs && state.active === previous.active) return;
  for (const workspaceId of restored) {
    window.clearTimeout(tabTimers.get(workspaceId));
    tabTimers.set(workspaceId, window.setTimeout(() => sendTabs(workspaceId), TABS_DELAY_MS));
  }
});

/** Put back what a workspace had open last run (once per window). */
export async function restoreWorkspace(workspaceId: string): Promise<void> {
  if (restored.has(workspaceId) || restoring.has(workspaceId)) return;
  restoring.add(workspaceId);
  try {
    const state = await fetchEditorState(workspaceId);
    const paths = new Set(state.tabs.map((tab) => tab.path));
    for (const backup of state.backups) {
      queueRestore(fileKeyOf(workspaceId, backup.path), backup);
      // A backup always gets a tab, even if its tab record was lost.
      if (!paths.has(backup.path)) state.tabs.push({ path: backup.path, mode: "edit", preview: false });
    }
    const store = useCodeEditorStore.getState();
    store.restoreTabs(workspaceId, state.tabs, state.active);
    // The dot shows (and the bundle reload waits) before the file is opened.
    for (const backup of state.backups) store.patchFile(fileKeyOf(workspaceId, backup.path), { dirty: true });
    lastSent.set(workspaceId, snapshot(workspaceId).key);
    restored.add(workspaceId);
  } catch (error) {
    // Nothing to restore is a normal first run; the next mount asks again.
    console.warn("Editor state could not be restored:", (error as Error).message);
  } finally {
    restoring.delete(workspaceId);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    flushBackups();
    for (const workspaceId of restored) sendTabs(workspaceId, true);
  });
}
