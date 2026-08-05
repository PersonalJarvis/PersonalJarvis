/**
 * Turning a coding CLI's screen into a conversation.
 *
 * The pane behind this chat is real and stays real — a coding agent is only
 * alive while something is attached to its pseudo-terminal — but nobody should
 * have to READ a terminal to follow what their agent is doing. The backend
 * already replays the screen into ordered, decoration-free lines
 * (`jarvis/agentic_ide/transcript.py`); this classifies those lines into the
 * four things a reader actually wants to tell apart:
 *
 * * what THEY said,
 * * what the agent said back,
 * * the steps it took to get there (tools, files, commands),
 * * and the helpers it spawned to do it.
 *
 * ## Why classification and not parsing
 *
 * A TUI is a picture, not a protocol. There is no framing to parse and no
 * guarantee a future release keeps any particular glyph, so this reads SHAPE —
 * a leading prompt caret, a bullet, an indent — and never a product's exact
 * wording. An unrecognised line is not dropped and not guessed at: it joins the
 * current block as prose. That is the fail-safe direction. A classifier that
 * discarded what it did not recognise would hide exactly the output that
 * matters on the day a CLI changes something.
 *
 * The banner a CLI prints on startup is the one thing removed outright: it is
 * the same eight lines every time, it is about the tool rather than the work,
 * and leaving it in means every conversation opens on someone else's logo.
 */

/** What one block of the rendered conversation is. */
export type ChatEventKind = "user" | "assistant" | "step" | "subagent" | "status";

export interface ChatEvent {
  kind: ChatEventKind;
  /** Body text, already joined and trimmed of trailing blanks. */
  text: string;
  /** Short label for a step or sub-agent — the part worth showing collapsed. */
  label?: string;
}

/**
 * Lines a CLI prints about ITSELF rather than about the work.
 *
 * Matched loosely and case-insensitively on purpose: these are banners, and a
 * banner that changes its capitalisation between releases should not start
 * leaking back into the conversation.
 */
const BANNER = [
  /^[\s\u2502\u2503|]*claude code\b/i,
  /^[\s\u2502\u2503|]*codex\b.*\bv\d/i,
  /^\s*(opus|sonnet|haiku|gpt|gemini)\b.*\b(context|effort|max|plus|pro)\b/i,
  /^\s*~[\/]/,
  /^\s*\d+\s+mcp servers?\b/i,
  /welcome to/i,
  /^\s*auto mode (on|off)\b/i,
  /shift\+tab to cycle/i,
  /^\s*[\u2190\u2192]\s*\d+\s+agents?\s*$/i,
  /^\s*for shortcuts\b/i,
  // The CLI's PERSISTENT chrome — the rows it repaints below its input box on
  // every frame. They are identical on turn one and turn fifty, they describe
  // the tool's settings rather than the work, and left in they arrive between
  // every pair of sentences the agent says.
  /^\s*[\u{1F4C1}\u{1F33F}\u{1F331}]/u,
  /^\s*(high|medium|low|minimal)\s*[\u00b7\u2022]\s*\/?(effort|thinking)/i,
  /^\s*\/(effort|model|mcp|help|clear|resume|status)\b/i,
  /^\s*\?\s*for shortcuts/i,
  /^\s*esc to interrupt/i,
  /\btokens?\b.*\b(used|left|remaining)\b/i,
];

/**
 * A row that is picture, not text.
 *
 * Coding CLIs draw a mascot, a logo or a box frame out of block glyphs. Those
 * rows survive into the transcript as runs of block and box-drawing
 * characters, and rendered in a proportional font they are a smear of grey
 * rectangles — which is exactly what they looked like. A line whose visible
 * characters are mostly drawing glyphs is dropped, whatever it spells.
 */
const DRAWING = /[\u2500-\u259f\u25a0-\u25ff\u2b1b-\u2b1f]/gu;

function isDrawing(line: string): boolean {
  const visible = line.replace(/\s/g, "");
  if (visible.length < 3) return false;
  const marks = visible.match(DRAWING)?.length ?? 0;
  return marks / visible.length > 0.4;
}

/** A line the user typed, as the CLI echoes it back. */
const USER_ECHO = /^[\s]*[>›❯]\s?(.*)$/;

/**
 * A step: the agent using a tool, reading a file, running something.
 *
 * Shape, not vocabulary — a bullet or a box-drawing lead-in followed by text.
 * The label is the first clause, which is what a collapsed row shows.
 */
const STEP = /^[\s]*(?:[●•⏺✻✽◆▸▪·]|[-*]\s)\s*(.+)$/;

/** A helper the agent started. Recognised by shape plus the ONE word that names it. */
const SUBAGENT = /\b(sub-?agent|subagent|task tool|spawn(?:ed|ing)?\s+agent)\b/i;

/** Spinner / elapsed-time rows a CLI repaints while it thinks. */
const STATUS = /^[\s]*[✻✽✢*·]?\s*\w+…?\s*(?:for\s+\d+\s*s|\(\d+s\))/i;

function isBanner(line: string): boolean {
  return BANNER.some((pattern) => pattern.test(line));
}

/** First clause of a step, for the collapsed row. Never longer than a line. */
function labelOf(text: string): string {
  const cut = text.split(/[(:—]/)[0]?.trim() ?? text;
  return (cut.length > 72 ? `${cut.slice(0, 72)}…` : cut) || text.slice(0, 72);
}

/**
 * Group transcript lines into conversation blocks.
 *
 * Consecutive lines of the same kind become ONE block, because a paragraph the
 * agent printed over four lines is one thing it said, not four. The order of
 * the input is preserved exactly: this never reorders, and never invents a
 * block that had no line behind it.
 */
export function readTranscript(lines: readonly string[]): ChatEvent[] {
  const events: ChatEvent[] = [];
  let current: ChatEvent | null = null;
  let pending: string | null = null;

  const push = (kind: ChatEventKind, text: string, label?: string) => {
    if (current && current.kind === kind && kind !== "user" && kind !== "step") {
      current.text += `\n${text}`;
      return;
    }
    current = { kind, text, label };
    events.push(current);
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) {
      // A blank line ends the current block rather than joining it: it is the
      // only paragraph break a terminal has.
      current = null;
      continue;
    }
    if (isBanner(line) || isDrawing(line)) continue;

    const echoed = USER_ECHO.exec(line);
    if (echoed) {
      const said = echoed[1].trim();
      // The empty caret is the CLI's input box waiting, not something said.
      if (said) push("user", said);
      else current = null;
      continue;
    }

    if (STATUS.test(line)) {
      // Kept as ONE trailing marker rather than as history: a CLI repaints its
      // spinner hundreds of times per turn, and every repaint that survived
      // became another line between two sentences.
      current = null;
      pending = line.trim();
      continue;
    }

    const step = STEP.exec(line);
    if (step) {
      const body = step[1].trim();
      push(SUBAGENT.test(body) ? "subagent" : "step", body, labelOf(body));
      continue;
    }

    // Anything else is the agent talking. Indented continuations join the block
    // above them, which is what makes a wrapped paragraph read as a paragraph.
    push("assistant", line.trim());
  }

  const kept = events.filter((event) => event.text.trim().length > 0);
  // The live spinner belongs at the END, where it means "still working", and
  // nowhere else.
  if (pending) kept.push({ kind: "status", text: pending });
  return kept;
}

/** Sub-agents seen in a conversation, newest last — the right-hand panel's list. */
export function subagentsOf(events: readonly ChatEvent[]): ChatEvent[] {
  return events.filter((event) => event.kind === "subagent");
}
