/**
 * Carrying a skill from the side panel's Skills tab to a terminal pane.
 *
 * A skill travels under its own drag type, the way an explorer row does
 * (`WORKSPACE_PATH_TYPE` in ./paneDrop): the pane recognises it during the
 * drag from `DataTransfer.types` alone, and only on the drop reads the
 * payload — browsers seal a drag's data until then. The text also rides along
 * as `text/plain`, so dropping a skill into any ordinary text field (or out of
 * the app) still hands over the Markdown.
 *
 * `text/plain` alone never arms a pane: that is what an accidental text drag
 * looks like (BUG-110), so the pane keys on the skill type, never on text.
 */

import { fill, translate } from "@/i18n";
import { useEventStore } from "@/store/events";

/** The drag type a skill card loads. */
export const SKILL_DRAG_TYPE = "application/x-jarvis-skill";

/**
 * The window event that pastes a skill into one named pane without a drag —
 * the card's "Paste into …" button, and the keyboard route to the same thing.
 */
export const PANE_PASTE_EVENT = "jarvis:ide-pane-paste";

export interface PanePasteDetail {
  workspaceId: string;
  pane: string;
  skill: SkillDragPayload;
}

/** What a dropped or pasted skill carries: enough to paste and to say what landed. */
export interface SkillDragPayload {
  id: string;
  title: string;
  content: string;
  hue: string;
}

/** Load a drag with one skill. */
export function loadSkillDrag(dt: DataTransfer, skill: SkillDragPayload): void {
  dt.effectAllowed = "copy";
  dt.setData(SKILL_DRAG_TYPE, JSON.stringify(skill));
  dt.setData("text/plain", skill.content);
}

/** Is a skill in this drag? Answerable mid-drag (types only). */
export function dragCarriesSkill(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types ?? []).includes(SKILL_DRAG_TYPE);
}

/** The skill a drop carried, or null for anything else. Read synchronously. */
export function readSkillDrag(dt: DataTransfer | null): SkillDragPayload | null {
  if (!dt) return null;
  const raw = dt.getData(SKILL_DRAG_TYPE);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<SkillDragPayload>;
    if (typeof value?.id !== "string" || typeof value.content !== "string") return null;
    return {
      id: value.id,
      title: typeof value.title === "string" ? value.title : "",
      content: value.content,
      hue: typeof value.hue === "string" ? value.hue : "blue",
    };
  } catch {
    // A foreign page faking the type with junk: not a skill, nothing to paste.
    return null;
  }
}

/** The slice of an xterm terminal a skill paste needs. */
export interface SkillPasteTerminal {
  paste(text: string): void;
  focus(): void;
  readonly modes: { readonly bracketedPasteMode: boolean };
}

/**
 * Paste a skill's text into a terminal — only where it cannot run by itself.
 *
 * A coding agent (Claude Code, Codex) turns on bracketed paste, so a
 * multi-line text arrives in its prompt as ONE block and waits there. A plain
 * shell that never turned it on reads every newline as Enter and would run
 * each Markdown line as a command. So a multi-line skill goes only into a
 * terminal in bracketed-paste mode; a single line is safe anywhere, since
 * `paste` never adds the final Enter. Returns whether the text was pasted.
 */
export function pasteSkillText(term: SkillPasteTerminal, text: string): boolean {
  if (!text) return false;
  if (text.includes("\n") && !term.modes.bracketedPasteMode) return false;
  term.paste(text);
  term.focus();
  return true;
}

/** Say why a skill did not land: the pane would have run it line by line. */
export function announceSkillRefused(pane: string): void {
  useEventStore.getState().pushToast("warning", fill(translate("ide_side_panel.skills.paste_refused"), { pane }));
}

/** Ask one pane to paste a skill, as a drop on it would. */
export function pasteSkillIntoPane(detail: PanePasteDetail): void {
  window.dispatchEvent(new CustomEvent<PanePasteDetail>(PANE_PASTE_EVENT, { detail }));
}
