import type { Terminal } from "@xterm/xterm";

/** A missing end marker must not leave a terminal frozen. */
export const SYNCHRONIZED_OUTPUT_TIMEOUT_MS = 1_000;

interface RenderService {
  _renderRows(start: number, end: number): void;
}

export interface SynchronizedOutput {
  reset(): void;
  dispose(): void;
}

/**
 * Honor DEC synchronized output (CSI ? 2026 h/l) on xterm 5.5.
 *
 * Codex splits one repaint across several PTY/WebSocket chunks. Without this
 * mode, xterm paints the intermediate erase and cursor moves before the rest
 * of the frame arrives. Parsing must continue; only presentation waits.
 *
 * RenderService is shared by the DOM, canvas and WebGL renderers, including
 * renderer replacements after context loss. Its debouncer calls _renderRows
 * through the service, so this also holds refreshes queued before the marker.
 * Keep the previous frame visible instead of hiding or clearing the canvas.
 */
export function installSynchronizedOutput(term: Terminal): SynchronizedOutput {
  const service = (term as unknown as {
    _core?: { _renderService?: RenderService };
  })._core?._renderService;
  if (typeof service?._renderRows !== "function") {
    // Unknown xterm internals: ordinary rendering remains available.
    return { reset() {}, dispose() {} };
  }

  const original = service._renderRows;
  let holding = false;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const release = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (!holding) return;
    holding = false;
    if (!disposed) term.refresh(0, Math.max(0, term.rows - 1));
  };
  const begin = () => {
    if (disposed || holding) return;
    holding = true;
    // Repeated set-mode commands are not nested frames and must not extend
    // the deadline indefinitely when a program forgets to send its end marker.
    timer = setTimeout(release, SYNCHRONIZED_OUTPUT_TIMEOUT_MS);
  };
  const renderRows = function (this: RenderService, start: number, end: number) {
    if (!holding) original.call(this, start, end);
  };
  service._renderRows = renderRows;

  const handlers = [
    term.parser.registerCsiHandler({ prefix: "?", final: "h" }, (params) => {
      if (params.includes(2026)) begin();
      // Let xterm apply other modes combined with this one, e.g. cursor mode.
      return false;
    }),
    term.parser.registerCsiHandler({ prefix: "?", final: "l" }, (params) => {
      if (params.includes(2026)) release();
      return false;
    }),
    term.parser.registerEscHandler({ final: "c" }, () => {
      release();
      return false;
    }),
  ];

  return {
    reset: release,
    dispose() {
      if (disposed) return;
      disposed = true;
      release();
      for (const handler of handlers) handler.dispose();
      if (service._renderRows === renderRows) service._renderRows = original;
    },
  };
}
