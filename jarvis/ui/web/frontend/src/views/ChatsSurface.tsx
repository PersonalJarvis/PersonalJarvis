import { HomeView } from "@/views/HomeView";

/**
 * The "chats" section: the front page (views/HomeView) — the voice stage
 * with the Jarvis bar, or the typed chat, chosen by the switch at the top of
 * the sidebar.
 *
 * Until 2026-08-23 this was the mission deck with the classic two-pane chat
 * one switch behind it. The deck is off the front page now (maintainer: no
 * dashboard to read before you can speak); its code stays in
 * views/MissionDeckView for the day it returns as a section of its own.
 */
export function ChatsSurface() {
  return <HomeView />;
}
