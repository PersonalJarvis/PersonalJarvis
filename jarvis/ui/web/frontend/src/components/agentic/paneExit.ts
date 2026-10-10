/**
 * Turn a dead pane's exit code into a sentence a person can act on.
 *
 * The pane used to print the number raw: "[Codex exited — code 4294967295]".
 * That value is a Windows exit code read as unsigned — the agent had really
 * ended with -1 — and ten digits of it told the user nothing except that
 * something had gone wrong in a way they could not look up. The backend now
 * re-signs the code once (`jarvis/terminal/pty_manager.py::normalize_exit_code`)
 * and this is the other half: naming the handful of codes that mean something
 * specific, and otherwise saying plainly that the agent stopped on its own.
 *
 * Deliberately NOT a lookup of every possible status. A wrong-but-confident
 * explanation is worse than none, so only codes whose meaning is unambiguous
 * are named; everything else keeps the number, which is the part worth
 * searching for.
 */

import { fill, translate } from "@/i18n";

/**
 * Windows NTSTATUS values a coding CLI actually dies with, already re-signed
 * the way the backend hands them over. The hex form is what a user would find
 * a description of, so it stays in the text.
 */
const WINDOWS_STATUS: ReadonlyMap<number, string> = new Map([
  [-1073741510, "ide_panes.exit.ctrl_c"],
  [-1073741819, "ide_panes.exit.access_violation"],
  [-1073740791, "ide_panes.exit.stack_overrun"],
  [-1073740940, "ide_panes.exit.heap_corruption"],
  [-1073740771, "ide_panes.exit.unhandled_exception"],
  [-1073741502, "ide_panes.exit.dll_missing"],
]);

/** What the pane writes into the terminal when its agent is gone. */
export function describeExit(name: string, code: number): string {
  return `[${name} ${explainExit(code)}]`;
}

/** The same explanation without the pane's name — for the header's tooltip. */
export function explainExit(code: number): string {
  if (code === 0) return translate("ide_panes.exit.stopped");
  const known = WINDOWS_STATUS.get(code);
  if (known) return translate(known);
  // -1 is what both a child that exited with -1 and a backend that could not
  // read a code at all arrive as. They are one case to the reader: the agent is
  // gone and did not stop cleanly.
  if (code === -1) return translate("ide_panes.exit.unexpected");
  return fill(translate("ide_panes.exit.unexpected_code"), { code });
}
