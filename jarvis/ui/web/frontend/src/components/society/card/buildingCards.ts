/**
 * What a building on the island IS, for its card: which model to draw, the
 * strings that name and explain it, the app section it stands for, and — for
 * a hub — whose live contents the card lists (HubDrawer's loaders, so the
 * card and the drawer never disagree about what is installed).
 *
 * Only places with a model of their own have a card: the five kit hubs and
 * the Memory House. The rest of the island is scenery (islandLayout.PlaceId).
 */
import type { SectionId } from "@/store/events";

import type { BuildingModelId } from "../figures/BuildingViewer";
import type { KitPlace, PlaceId } from "../world/islandLayout";

export type BuildingPlace = KitPlace | "archive";

export interface BuildingCard {
  place: BuildingPlace;
  model: BuildingModelId;
  /** `society.world.<key>` — the place's name, the same the map label shows. */
  nameKey: string;
  /** `society.world.<key>` — one line on what it stands for (the drawer's hint). */
  taglineKey: string;
  /** `society.world.<key>` — what it does, a paragraph. */
  doesKey: string;
  /** `society.world.<key>` — how a person uses it, a paragraph. */
  howKey: string;
  /** The app section this building stands for. */
  section: SectionId;
  /** The hub whose live contents the card lists; null for a building that lists nothing. */
  hub: KitPlace | null;
  /** The Foundry builds agents: its card offers the creator. */
  builds?: boolean;
}

export const BUILDING_CARDS: Record<BuildingPlace, BuildingCard> = {
  plugins: {
    place: "plugins",
    model: "plugin-docks",
    nameKey: "place_plugins",
    taglineKey: "drawer_plugins_hint",
    doesKey: "card_plugins_does",
    howKey: "card_plugins_how",
    section: "plugins",
    hub: "plugins",
  },
  foundry: {
    place: "foundry",
    model: "agent-foundry",
    nameKey: "place_foundry",
    taglineKey: "drawer_foundry_hint",
    doesKey: "card_foundry_does",
    howKey: "card_foundry_how",
    section: "agents",
    hub: null,
    builds: true,
  },
  skills: {
    place: "skills",
    model: "skill-forge",
    nameKey: "place_skills",
    taglineKey: "drawer_skills_hint",
    doesKey: "card_skills_does",
    howKey: "card_skills_how",
    section: "skills",
    hub: "skills",
  },
  mcp: {
    place: "mcp",
    model: "relay-tower",
    nameKey: "place_mcp",
    taglineKey: "drawer_mcp_hint",
    doesKey: "card_mcp_does",
    howKey: "card_mcp_how",
    section: "mcps",
    hub: "mcp",
  },
  cli: {
    place: "cli",
    model: "terminal-cantina",
    nameKey: "place_cli",
    taglineKey: "drawer_cli_hint",
    doesKey: "card_cli_does",
    howKey: "card_cli_how",
    section: "clis",
    hub: "cli",
  },
  archive: {
    place: "archive",
    model: "memory-house",
    nameKey: "place_archive",
    taglineKey: "drawer_memory_hint",
    doesKey: "card_archive_does",
    howKey: "card_archive_how",
    section: "memory",
    hub: null,
  },
};

export function isBuildingPlace(place: PlaceId): place is BuildingPlace {
  return place in BUILDING_CARDS;
}
