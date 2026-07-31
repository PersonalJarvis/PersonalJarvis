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

/**
 * Windows NTSTATUS values a coding CLI actually dies with, already re-signed
 * the way the backend hands them over. The hex form is what a user would find
 * a description of, so it stays in the text.
 */
const WINDOWS_STATUS: ReadonlyMap<number, string> = new Map([
  [-1073741510, "stopped with Ctrl-C (0xC000013A)"],
  [-1073741819, "crashed — memory access violation (0xC0000005)"],
  [-1073740791, "crashed — stack buffer overrun (0xC0000409)"],
  [-1073740940, "crashed — heap corruption (0xC0000374)"],
  [-1073740771, "crashed — unhandled exception (0xC000041D)"],
  [-1073741502, "could not start — a required DLL was missing (0xC0000142)"],
]);

/** What the pane writes into the terminal when its agent is gone. */
export function describeExit(name: string, code: number): string {
  return `[${name} ${explainExit(code)}]`;
}

/** The same explanation without the pane's name — for the header's tooltip. */
export function explainExit(code: number): string {
  if (code === 0) return "stopped";
  const known = WINDOWS_STATUS.get(code);
  if (known) return known;
  // -1 is what both a child that exited with -1 and a backend that could not
  // read a code at all arrive as. They are one case to the reader: the agent is
  // gone and did not stop cleanly.
  if (code === -1) return "stopped unexpectedly — use Restart to bring it back";
  return `stopped unexpectedly (exit code ${code}) — use Restart to bring it back`;
}
