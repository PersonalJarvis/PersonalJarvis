/**
 * Shift+Enter writes a new line instead of sending the message.
 *
 * ## Why this file has to exist
 *
 * A terminal has no concept of "insert a line break without submitting": Enter
 * is one byte, carriage return, and every modifier combination xterm knows
 * sends that same byte. So a long instruction typed into a coding agent's
 * prompt box could not be broken into paragraphs at all — Shift+Enter sent it
 * half-written (reported 2026-07-27), which is worse than doing nothing,
 * because the agent immediately starts working on the fragment.
 *
 * Native terminals solve this by being configured for it: on macOS the
 * convention is Option+Enter, and Claude Code's own `/terminal-setup` writes a
 * Shift+Enter mapping into iTerm2 and VS Code. A pane inside this app has no
 * such settings file for anyone to edit, so the mapping belongs here.
 *
 * ## What is actually sent, and why that sequence
 *
 * `ESC` followed by carriage return — what any terminal sends for
 * Alt/Option+Enter, and the reason it is the right choice is that it is not a
 * private convention:
 *
 * * **Coding agents** built on Ink (Claude Code, Gemini CLI) parse a leading
 *   ESC as the meta modifier, so this arrives as the meta+Enter their prompt
 *   box already treats as a line break — the same keystroke their macOS users
 *   press.
 * * **A plain shell pane** is served too: PSReadLine, the default on Windows,
 *   binds Alt+Enter to inserting a line.
 * * **Nothing submits on it.** Where a program does not know the sequence it
 *   ignores it, which is the honest failure here — a stray line break costs a
 *   keystroke, a stray submit costs a half-written instruction.
 *
 * Both bytes go out in ONE write on purpose. Readline-style parsers decide
 * "ESC as a modifier" versus "the Escape key, then a separate Return" by
 * whether the bytes arrive together, so splitting them would cancel the
 * agent's prompt instead of extending it.
 *
 * ## Which chords count
 *
 * Shift, Alt/Option and Cmd — every "Enter, but not really" a user might
 * reach for. Ctrl+Enter is deliberately left alone: Ctrl+<key> is a control
 * code in a terminal, and people who use Ctrl+Enter to send would lose that.
 * Ctrl also rules out AltGr, which European layouts deliver as Ctrl+Alt.
 */

/** ESC + CR — Alt/Option+Enter, as a real terminal would send it. */
export const NEWLINE_SEQUENCE = "\x1b\r";

/** The slice of an xterm terminal this bridge drives. */
export interface NewlineCapableTerminal {
  attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean): void;
  /** Send data as if the user had typed it (fires xterm's `onData`). */
  input(data: string, wasUserInput?: boolean): void;
}

/** Is this the keystroke the user means as "new line, do not send"? */
export function isNewlineChord(event: KeyboardEvent): boolean {
  if (event.key !== "Enter") return false;
  // Mid-composition (Japanese, Chinese, Korean input) Enter confirms the
  // candidate word and never reaches the terminal as a keystroke at all.
  if (event.isComposing) return false;
  if (event.ctrlKey) return false;
  return event.shiftKey || event.altKey || event.metaKey;
}

/**
 * Wire the newline chord into `term`, returning a cleanup function.
 *
 * `preventDefault` matters as much as the return value: without it the browser
 * still runs its default action for the key, xterm's separate keypress path
 * sees a plain Enter, and the carriage return this exists to prevent goes out
 * after all.
 */
export function installNewlineBridge(term: NewlineCapableTerminal): () => void {
  let live = true;

  term.attachCustomKeyEventHandler((event) => {
    if (!live) return true;
    if (!isNewlineChord(event)) return true;
    // keydown carries the keystroke; the keypress and keyup that follow are the
    // same press and must be swallowed rather than acted on twice.
    if (event.type === "keydown") {
      event.preventDefault();
      term.input(NEWLINE_SEQUENCE);
    }
    return false;
  });

  return () => {
    live = false;
  };
}
