/**
 * Module-level Map fuer xterm.js-Terminal-Instanzen.
 *
 * Lebt EXPLIZIT ausserhalb des Zustand-Stores: jeder PTY-Stream-Chunk wuerde
 * sonst einen kompletten React-Re-Render des Mission-Views ausloesen, was die
 * UI bei beschaeftigten Workern (mehrere KB/s Output) zum Stehen bringt.
 *
 * Lifecycle: PtyTerminal-Component registriert beim Mount, dispose im Cleanup.
 * Bei React-StrictMode laufen Mount/Unmount zweimal — die Registry-Eintraege
 * werden im ersten Cleanup wieder freigegeben, das ist okay.
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
      // dispose ist defensiv — wenn xterm intern bereits zerstoert ist,
      // dann doppelter dispose-Aufruf darf den Cleanup nicht killen
    }
    registry.delete(workerId);
  }
}

export function clearAllTerminals(): void {
  for (const [id] of registry) disposeTerminal(id);
}
