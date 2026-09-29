/**
 * A left-button drag in a pane always selects text.
 *
 * Coding-agent CLIs (Claude Code, Codex, ...) negotiate mouse tracking the
 * moment they start. From then on xterm hands every press to the CLI as a
 * mouse report and disables its own selection, so holding the left button and
 * dragging across the output selected nothing at all — only xterm's hidden
 * escape hatch (Shift on Windows/Linux, Option on macOS) still selected. Nobody
 * knows that chord, and copying an agent's output is something people do all
 * day.
 *
 * So an UNMODIFIED primary press is marked as that escape-hatch chord before
 * xterm sees it: xterm then keeps it away from the CLI and starts a selection,
 * exactly as if the chord had been held. Everything else still reaches the CLI
 * as before — the right and middle buttons, the wheel, hover motion, and any
 * press with a modifier held (Ctrl/Alt-click is the way to click INTO a CLI
 * that wants the mouse).
 *
 * Only public DOM is touched: the event is marked in the capture phase on the
 * pane's container, which runs before both of xterm's own listeners on the
 * elements inside it. macOS additionally needs xterm's
 * `macOptionClickForcesSelection` option, which the pane sets.
 */

/** The xterm surface this module reads — narrowed so tests can hand in fakes. */
export interface MouseModeSource {
  modes: { mouseTrackingMode: string };
}

/**
 * Mark plain primary presses inside `container` as selection presses while the
 * CLI in `term` has mouse tracking on. Returns the disposer.
 */
export function installMouseSelection(
  container: HTMLElement,
  term: MouseModeSource,
  isMac: boolean,
): () => void {
  const onMouseDown = (event: MouseEvent) => {
    if (event.button !== 0) return;
    if (event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
    if ((term.modes?.mouseTrackingMode ?? "none") === "none") return;
    // Own properties shadow the prototype getters for every later listener of
    // this one dispatch; the browser itself never reads them back.
    Object.defineProperty(event, isMac ? "altKey" : "shiftKey", { value: true });
  };
  container.addEventListener("mousedown", onMouseDown, { capture: true });
  return () => container.removeEventListener("mousedown", onMouseDown, { capture: true });
}
