/**
 * Which of THIS window's viewers of a pane decides the pane's size.
 *
 * A pseudo-terminal has one size, and the server hands it to one viewer at a
 * time (see `claim` in ./paneSocket). Across windows, the window the user is
 * in wins, and a gesture anywhere in a window takes back every pane another
 * window sized (`reclaimOnGesture` in ./AgenticTerminal). Inside ONE window
 * that rule has no answer: two viewers of the same pane — the office's pane
 * window and the same pane in the IDE grid behind it — both see the gesture,
 * both are "in front", and each took the size back from the other every two
 * seconds. The agent redrew its whole screen for each hand-over, alternating
 * between a wide and a narrow layout: the office window flickered and showed
 * its text squeezed into the left third (2026-09-29).
 *
 * So within a window a pane has at most one LEAD viewer: the one opened as the
 * pane's focus (the office's window, while it is open) or the one the user
 * last pressed. Every other viewer of that pane follows the lead's geometry
 * and never claims on its own. When the lead goes away, the rest are told so
 * they can take the size back for the tile they show.
 *
 * Module state on purpose: it is per window by construction, which is exactly
 * the scope of the problem — separate windows keep the server's rule.
 */

/** A viewer's identity in the registry; any unique object will do. */
export type SizeLeadToken = object;

const leads = new Map<string, SizeLeadToken>();
const released = new Map<string, Set<() => void>>();

/** The registry key for one pane: the same name can live in two workspaces. */
export function sizeLeadKey(workspaceId: string | undefined, name: string): string {
  return `${workspaceId ?? ""}\u0000${name}`;
}

/** Make `token` the viewer that sizes the pane in this window. */
export function takeSizeLead(key: string, token: SizeLeadToken): void {
  leads.set(key, token);
}

/**
 * Give the lead up, if `token` still holds it, and tell the pane's other
 * viewers so one of them can size the pane again. A viewer that already lost
 * the lead to a later press releases nothing.
 *
 * The others hear it a microtask later, and only if nobody took the lead in
 * between: a lead viewer that merely rebuilds its terminal (its connect effect
 * re-running in the same commit) must not hand the size to the grid and take
 * it straight back — two full repaints of the agent for nothing.
 */
export function releaseSizeLead(key: string, token: SizeLeadToken): void {
  if (leads.get(key) !== token) return;
  leads.delete(key);
  queueMicrotask(() => {
    if (leads.has(key)) return;
    for (const listener of [...(released.get(key) ?? [])]) listener();
  });
}

/** Does `token` hold the pane's lead right now (not merely "nobody leads")? */
export function holdsSizeLead(key: string, token: SizeLeadToken): boolean {
  return leads.get(key) === token;
}

/** May `token` size the pane: it leads, or nobody in this window does. */
export function mayLeadSize(key: string, token: SizeLeadToken): boolean {
  const lead = leads.get(key);
  return lead === undefined || lead === token;
}

/** Call `listener` whenever the pane's lead is released. Returns the unsubscribe. */
export function onSizeLeadReleased(key: string, listener: () => void): () => void {
  let set = released.get(key);
  if (!set) {
    set = new Set();
    released.set(key, set);
  }
  set.add(listener);
  return () => {
    const current = released.get(key);
    if (!current) return;
    current.delete(listener);
    if (current.size === 0) released.delete(key);
  };
}
