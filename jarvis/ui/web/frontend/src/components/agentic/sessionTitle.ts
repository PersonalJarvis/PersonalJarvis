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
 *   1. the recap — pinned by the user, written by the model, read off the
 *      screen on the last header poll, the last prompt sent, or the message
 *      that opened the CLI's own conversation (`known_headline` on the
 *      backend, which orders them);
 *   2. the opening line of the last prompt, for a backend that answered with
 *      no recap at all — the closest thing a terminal has to a chat's first
 *      message;
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
 * Section labels a composed brief is structured with — the same set the
 * backend's `_job_line` knows. On their own they name the FORM of the prompt,
 * not the work, so the job is on the line after them; where one prefixes the
 * sentence ("Task: fix the login test") the sentence is the part worth showing.
 */
const SECTION_LABELS = new Set([
  "brief",
  "context",
  "goal",
  "goals",
  "instruction",
  "instructions",
  "objective",
  "objectives",
  "prompt",
  "request",
  "summary",
  "task",
  "tasks",
  "what to do",
  "your task",
]);

/** A line without its heading marks and emphasis, whitespace collapsed. */
function bare(row: string): string {
  return row
    .replace(/^\s*#{1,6}\s*/, "")
    .replace(/[*_`]{1,3}/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The first line of a prompt that says something.
 *
 * A composed brief runs to paragraphs and opens with "## Task"; a title is one
 * line, and the row's own width clips it further. The first SAYING line rather
 * than the first N characters: the cut then lands between sentences instead of
 * inside a word — and never on the heading, which is the same for every brief.
 * A brief that is nothing but labels still answers with its first label rather
 * than with nothing.
 */
export function promptOpening(prompt: string): string {
  let label = "";
  for (const line of prompt.split(/\r?\n/).slice(0, 12).map(bare).filter(Boolean)) {
    const colon = line.indexOf(":");
    if (colon > 0 && SECTION_LABELS.has(line.slice(0, colon).trim().toLowerCase())) {
      const rest = line.slice(colon + 1).trim();
      if (rest) return rest;
      label = label || line;
      continue;
    }
    if (SECTION_LABELS.has(line.replace(/:$/, "").trim().toLowerCase())) {
      label = label || line;
      continue;
    }
    return line;
  }
  return label;
}
