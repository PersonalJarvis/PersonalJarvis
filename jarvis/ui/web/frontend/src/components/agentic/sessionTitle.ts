import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

/**
 * What a session is called in a list of them: what it is ABOUT, not which
 * CLI runs it.
 *
 * Nine rows all reading "Claude Code" told the user nothing — the logo in
 * front of each row already says which CLI it is, and the call-sign at the end
 * says which pane; the label between them was the one place the list could
 * say what the conversation is for, and it repeated the logo instead
 * (maintainer report 2026-08-27). So the label is the pane's title, the same
 * sentence the grid draws in that pane's header:
 *
 *   1. the recap — pinned by the user, written by the model, or the floor read
 *      off the screen on the last header poll (`known_headline` on the backend);
 *   2. the opening line of the last prompt, for a pane no header has described
 *      yet — the closest thing a terminal has to a chat's first message;
 *   3. the CLI's name, for a pane that has been asked nothing at all, where
 *      "Claude Code" is in fact everything there is to say.
 */
export function sessionTitle(
  pane: Pick<WorkspacePaneRow, "recap" | "last_prompt" | "display_name" | "name">,
): string {
  const recap = (pane.recap ?? "").trim();
  if (recap) return recap;
  const opening = promptOpening(pane.last_prompt ?? "");
  if (opening) return opening;
  return pane.display_name || pane.name;
}

/**
 * The first non-blank line of a prompt, whitespace collapsed.
 *
 * A composed brief runs to paragraphs; a title is one line, and the row's own
 * width clips it further. The first line rather than the first N characters,
 * because a brief opens with its instruction and the cut then lands between
 * sentences instead of inside a word.
 */
export function promptOpening(prompt: string): string {
  const line = prompt.split(/\r?\n/).find((row) => row.trim()) ?? "";
  return line.replace(/\s+/g, " ").trim();
}
