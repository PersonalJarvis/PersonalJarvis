import { expect, it } from "vitest";
import { readCompanionVisible, writeCompanionVisible } from "./preferences";

it("keeps presentation preferences separate for ordinary and swarm worlds", () => {
  writeCompanionVisible("test:ordinary", false);
  writeCompanionVisible("test:swarm:one", true);
  expect(readCompanionVisible("test:ordinary")).toBe(false);
  expect(readCompanionVisible("test:swarm:one")).toBe(true);
  localStorage.removeItem("jarvis.companion.test:ordinary.visible.v1");
  localStorage.removeItem("jarvis.companion.test:swarm:one.visible.v1");
});
