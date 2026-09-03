/**
 * Every checkpoint the backend can send has a place on the island.
 *
 * The gap this closes cost the town six buildings: `hub:comms`, `hub:desktop`,
 * `hub:web`, `hub:models` and `gallery` were in the shipped vocabulary's
 * superset for weeks with nowhere to stand, so the figure fell through to the
 * Workshop and the viewer saw "files and shell" for work that was nothing of
 * the kind. A checkpoint whose place is missing is a silent lie about what an
 * agent is doing — this test makes it a red build instead.
 */
import { describe, expect, it } from "vitest";

import { CHECKPOINTS } from "@/lib/societyApi";

import { BUILDING_CARDS } from "../card/buildingCards";
import { buildIsland, KIT_PLACES, type PlaceId } from "./islandLayout";
import { HUBS } from "./HubDrawer";
import { CHECKPOINT_PLACE } from "./Walkers";

describe("checkpoint places", () => {
  it("gives every shipped checkpoint a place the island can draw", () => {
    const { content } = buildIsland();
    for (const checkpoint of CHECKPOINTS) {
      expect(CHECKPOINT_PLACE, checkpoint).toHaveProperty(checkpoint);
      const place = CHECKPOINT_PLACE[checkpoint];
      // `idle` is the one checkpoint with no destination: the figure wanders.
      if (place === null) {
        expect(checkpoint).toBe("idle");
        continue;
      }
      expect(content.places[place as PlaceId], `${checkpoint} -> ${place}`).toBeDefined();
    }
  });

  it("never sends two different checkpoints to the same hall", () => {
    const halls = Object.entries(CHECKPOINT_PLACE)
      .filter(([, place]) => place !== null && (KIT_PLACES as readonly string[]).includes(place))
      .map(([, place]) => place);
    expect(new Set(halls).size).toBe(halls.length);
  });

  it("gives every kit hall a drawer and a card", () => {
    for (const place of KIT_PLACES) {
      expect(HUBS, place).toHaveProperty(place);
      expect(BUILDING_CARDS, place).toHaveProperty(place);
      // The card and the drawer name the building with the same locale key.
      expect(BUILDING_CARDS[place].taglineKey).toBe(HUBS[place].hintKey);
    }
  });

  it("gives every hub either live contents, a fixed set of hands, or a body", () => {
    for (const place of KIT_PLACES) {
      const cfg = HUBS[place];
      expect(Boolean(cfg.load || cfg.groups || cfg.bodyKey), place).toBe(true);
      // A fixed list must not be empty — an empty hall explains nothing.
      for (const group of cfg.groups ?? []) {
        expect(group.items.length, `${place}/${group.labelKey}`).toBeGreaterThan(0);
      }
    }
  });
});
