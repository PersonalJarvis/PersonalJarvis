import { create } from "zustand";

import { readHomeSurface, writeHomeSurface, type HomeSurface } from "@/lib/homeSurface";

/**
 * The front page's own store: which surface (voice / chat) is on screen.
 *
 * Lives apart from the big event store because it is read by exactly two
 * places — the sidebar switch that sets it and the home view that renders
 * it — and because nothing about it comes over the WebSocket.
 */
interface HomeStore {
  surface: HomeSurface;
  setSurface: (surface: HomeSurface) => void;
}

export const useHomeStore = create<HomeStore>((set) => ({
  surface: readHomeSurface(),
  setSurface: (surface) => {
    writeHomeSurface(surface);
    set({ surface });
  },
}));
