/**
 * Which surface the "chats" section shows: the mission deck, or the classic
 * chat view it replaced.
 *
 * The deck is a NEW front page, not a migration — the classic view keeps every
 * feature it ever had (conversation history, per-thread resume, delete, "speak
 * in this conversation") and stays one click away. Anything the deck cannot do
 * yet is therefore never lost, only somewhere else, which is the whole reason
 * this switch exists rather than a hard cutover.
 *
 * Persisted in localStorage so the choice survives a reload and a rebuild. A
 * broken or absent value falls back to the deck: a corrupted preference must
 * land somewhere usable, never on a blank screen.
 */

export type DeckMode = "deck" | "classic";

const STORAGE_KEY = "chats.surface.v1";
const DEFAULT_MODE: DeckMode = "deck";

function isDeckMode(v: unknown): v is DeckMode {
  return v === "deck" || v === "classic";
}

export function readDeckMode(): DeckMode {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isDeckMode(raw) ? raw : DEFAULT_MODE;
  } catch {
    // Private mode / storage disabled — the deck still has to render.
    return DEFAULT_MODE;
  }
}

export function writeDeckMode(mode: DeckMode): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* not being able to remember the choice must not break switching it */
  }
}
