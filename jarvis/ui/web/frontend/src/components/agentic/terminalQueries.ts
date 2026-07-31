/**
 * Stop this pane from answering the agent's terminal-protocol queries.
 *
 * A coding CLI asks its terminal what it is (`ESC [ c`) and which colours it
 * draws on (`ESC ] 10;?` and `ESC ] 11;?`) while it starts, and reads the
 * answer within milliseconds. Left to itself xterm answers those here, in the
 * browser — so the reply has to travel back over the WebSocket before it
 * reaches the CLI. Across a grid of panes that round trip regularly outlasts
 * the CLI's own wait, and the answer lands after the prompt editor has opened,
 * where it appears as a pasted-looking line of `11;rgb:1212/1414/1a1a` the
 * user never typed.
 *
 * The backend answers these instead, in the process that owns the PTY, where
 * there is no network to be late (`jarvis/agentic_ide/terminal_input.py`). So
 * the browser must stay quiet: a second answer is not harmless, it is exactly
 * the junk being fixed, arriving one round trip later.
 *
 * Only QUERIES are suppressed. `ESC ] 10 ; <colour>` also SETS the foreground,
 * and a program that recolours its terminal must keep working — those fall
 * through to xterm's own handler untouched.
 */

/** The slice of xterm's parser this needs — kept small so it can be tested. */
export interface TerminalQueryParser {
  registerOscHandler(
    ident: number,
    callback: (data: string) => boolean | Promise<boolean>,
  ): { dispose(): void };
  registerCsiHandler(
    id: { prefix?: string; intermediates?: string; final: string },
    callback: (params: unknown) => boolean | Promise<boolean>,
  ): { dispose(): void };
}

/** OSC 10 is the foreground colour, OSC 11 the background. */
const COLOUR_QUERIES = [10, 11];

/** True for `ESC ] 1x ; ?` — asking, as opposed to setting a colour. */
export function isColourQuery(data: string): boolean {
  return data.trim().startsWith("?");
}

/**
 * Silence this terminal's protocol replies. Returns a disposer that restores
 * xterm's own handling, so a pane can be torn down without leaking handlers.
 */
export function installQuerySuppression(parser: TerminalQueryParser): () => void {
  const handlers = COLOUR_QUERIES.map((ident) =>
    parser.registerOscHandler(ident, isColourQuery),
  );
  // Primary device attributes only — no prefix. The secondary form (`ESC [ > c`)
  // has its own identifier and is left alone, so nothing beyond the one query
  // that misbehaves changes behaviour.
  handlers.push(parser.registerCsiHandler({ final: "c" }, () => true));
  return () => {
    for (const handler of handlers) {
      try {
        handler.dispose();
      } catch {
        /* the terminal is already gone */
      }
    }
  };
}
