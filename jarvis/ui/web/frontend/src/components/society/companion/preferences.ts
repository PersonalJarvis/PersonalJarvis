/** Only presentation is persisted. World poses and task authority remain elsewhere. */
export function readCompanionVisible(worldKey: string): boolean {
  try { return localStorage.getItem(`jarvis.companion.${worldKey}.visible.v1`) !== "false"; }
  catch { return true; } // Storage can be unavailable in private browser profiles.
}
export function writeCompanionVisible(worldKey: string, visible: boolean): void {
  try { localStorage.setItem(`jarvis.companion.${worldKey}.visible.v1`, String(visible)); }
  catch { /* The current mounted view retains its in-memory preference. */ }
}
