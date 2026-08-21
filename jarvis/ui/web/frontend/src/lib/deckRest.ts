/**
 * When the board is AT REST — and what that changes.
 *
 * The board's bottom row holds five operational instruments: outputs, runs,
 * the last capture, the terminals and the coding workspace. On a machine
 * that is simply switched on, four of them say "nothing yet" and the fifth
 * lists runs from yesterday. The maintainer's verdict on that screen
 * (2026-08-21): boring — a third of the stage spent on five boxes that
 * apologise for being empty.
 *
 * So the row has two forms. When something is genuinely HAPPENING it is the
 * row of cards it always was. When nothing is, it collapses to one strip of
 * readouts (`DeckRestStrip`) and gives its height to the log, the orb and
 * the wiki. The rule behind that switch is here, on its own, so it can be
 * read and tested without a DOM:
 *
 *   at rest  ⇔  nothing is running, streaming or on screen RIGHT NOW
 *
 * HISTORY IS NOT ACTIVITY. Eighty-four finished runs and a hundred old
 * outputs are the instrument's SCALE, not its needle — the strip prints
 * those figures, so nothing is hidden by collapsing. What keeps the cards
 * open is only ever live: a mission still running, a run with no end, a
 * shell command in flight, terminal output this session, an IDE pane, a
 * capture inside its afterglow.
 *
 * The person can always override it: the strip has an expand control that
 * brings the full row back (`MissionDeckView`).
 */
export interface RestInputs {
  /** Missions still running — a finished one is history. */
  runningOutputs: number;
  /** Runs with no end timestamp. */
  liveRuns: number;
  /** Shell commands the brain has in flight. */
  shellRunning: number;
  /** Terminal lines this session — the card has a stream to show. */
  termLines: number;
  /** Panes in the agentic IDE, running or idle: a workspace IS open. */
  idePanes: number;
  /** A capture is on screen inside its afterglow. */
  captureShowing: boolean;
}

/**
 * True when none of the five instruments has anything live to show.
 *
 * Deliberately NOT "every card is empty": the runs card is never empty on a
 * used install, so an emptiness test would leave the row expanded forever on
 * exactly the screen this was written for.
 */
export function boardAtRest(i: RestInputs): boolean {
  return (
    i.runningOutputs === 0 &&
    i.liveRuns === 0 &&
    i.shellRunning === 0 &&
    i.termLines === 0 &&
    i.idePanes === 0 &&
    !i.captureShowing
  );
}
