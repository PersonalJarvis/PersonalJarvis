/**
 * Which appshot the editor shows. Set by a click on the appshot card in the
 * screen corner (the `AppshotEditRequested` event) or on the last appshot on
 * the Appshots page; `AppshotEditorHost` renders the editor over whatever view
 * is open while it is set — nothing navigates away.
 */
import { create } from "zustand";

interface AppshotEditorState {
  /** The appshot id to edit, or null when the editor is closed. */
  openId: string | null;
  /** Bumped whenever an edit replaced the held appshot, so previews refresh. */
  revision: number;
  open: (id: string) => void;
  close: () => void;
  applied: () => void;
}

export const useAppshotEditor = create<AppshotEditorState>((set) => ({
  openId: null,
  revision: 0,
  open: (id) => set({ openId: id }),
  close: () => set({ openId: null }),
  applied: () => set((state) => ({ revision: state.revision + 1 })),
}));

/**
 * What the app does with an `AppshotEditRequested` event (a click on the
 * appshot card). Only the main window acts — a detached solo window leaves it
 * to the main one, which the backend also brings to the front.
 *
 * Returns what happened, for the toast and for tests.
 */
export function handleAppshotEditRequest(
  payload: unknown,
  { solo }: { solo: boolean },
): "opened" | "gone" | "ignored" {
  if (solo) return "ignored";
  const id = (payload as { appshot_id?: unknown } | null)?.appshot_id;
  if (typeof id === "string" && id) {
    useAppshotEditor.getState().open(id);
    return "opened";
  }
  return "gone";
}
