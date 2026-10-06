/**
 * The section ids the Agentic IDE answers to — the sidebar's IDE face and the
 * caption's layout switch read the same list.
 *
 * Mirrors the nav row's own `matchIds` (see components/layout/navGroups): the
 * section has been renamed twice and the older ids are still what some entry
 * points set.
 */
export const IDE_SECTIONS: readonly string[] = ["agentic-ide", "chat-workspace", "agentic-ide-classic"];
