/**
 * Module-level map for xterm.js terminal instances.
 *
 * Lives EXPLICITLY outside the Zustand store: otherwise every PTY stream
 * chunk would trigger a full React re-render of the mission view, which
 * grinds the UI to a halt with busy workers (several KB/s of output).
 *
 * Lifecycle: the PtyTerminal component registers on mount, disposes in
 * cleanup. Under React StrictMode, mount/unmount runs twice — the registry
 * entries get freed again on the first cleanup, which is fine.
 */
import type { Terminal } from "@xterm/xterm";

const registry = new Map<string, Terminal>();

export function getTerminal(workerId: string): Terminal | undefined {
  return registry.get(workerId);
}

export function setTerminal(workerId: string, term: Terminal): void {
  registry.set(workerId, term);
}

export function disposeTerminal(workerId: string): void {
  const term = registry.get(workerId);
  if (term) {
    try {
      term.dispose();
    } catch {
      // dispose is defensive — if xterm has already destroyed itself
      // internally, a duplicate dispose call must not kill the cleanup
    }
    registry.delete(workerId);
  }
}

export function clearAllTerminals(): void {
  for (const [id] of registry) disposeTerminal(id);
}
