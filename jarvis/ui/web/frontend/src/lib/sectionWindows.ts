import type { SectionId } from "@/store/events";
import { SETTINGS_HUB_IDS } from "@/components/layout/navGroups";

/** A tab changes the content, never the identity of its native window. */
export function sectionWindow(section: SectionId): SectionId {
  if (SETTINGS_HUB_IDS.includes(section)) return "settings";
  if (["agentic-ide", "agentic-ide-classic", "chat-workspace"].includes(section)) return "agentic-ide";
  if (["plugins", "mcps", "skills"].includes(section)) return "plugins";
  if (section === "cli-test-hub") return "clis";
  return section;
}

export const DETACHABLE_SECTIONS: readonly SectionId[] = [
  "agentic-ide", "agents", "settings", "plugins", "clis", "docs", "memory",
  "board", "sessions", "marketplace", "visualization",
];

export function detachedWindowFor(section: SectionId, detached: readonly SectionId[]): SectionId | undefined {
  const owner = sectionWindow(section);
  return detached.find((view) => sectionWindow(view) === owner);
}

/** The URL keeps this identity when a Settings/Plugins tab changes or reloads. */
export function windowIdentity(search: string, fallback: SectionId): string {
  const params = new URLSearchParams(search);
  return params.get("window") || params.get("view") || sectionWindow(fallback);
}
