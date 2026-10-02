/**
 * Which appshot the editor should open. Set by a click on the appshot card in
 * the screen corner (the `AppshotEditRequested` event) or on the last appshot
 * on the Appshots page; the Appshots view renders the editor while it is set.
 */
import { create } from "zustand";

interface AppshotEditorState {
  /** The appshot id to edit, or null when the editor is closed. */
  openId: string | null;
  open: (id: string) => void;
  close: () => void;
}

export const useAppshotEditor = create<AppshotEditorState>((set) => ({
  openId: null,
  open: (id) => set({ openId: id }),
  close: () => set({ openId: null }),
}));
