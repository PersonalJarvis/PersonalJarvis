import { afterEach, describe, expect, it, vi } from "vitest";
import { parseViewPreferences, readViewPreferences, saveViewPreferences, VIEW_KEY, type CameraPose, type ViewPreferences } from "./viewPreferences";
import { WORLD } from "./world";

const pose: CameraPose = { position: [320, 100, 150], target: [320, 76, 50] };
const preferences: ViewPreferences = { mode: "follow", followAgentId: "comms", viewpoint: "rear", neutral: true, shadows: false, pose };
const saved = { ...preferences, world_id: WORLD.world_id, layout_version: WORLD.layout_version };
const parse = (change: Record<string, unknown>) => parseViewPreferences(JSON.stringify({ ...saved, ...change }));

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe("Mars view preferences", () => {
  it("restores and persists the close reference preset within its world and layout", () => {
    const close: ViewPreferences = { ...preferences, mode: "outpost", followAgentId: null, viewpoint: "close_reference" };
    expect(parse({ ...close })).toEqual(close);
    saveViewPreferences(close);
    expect(readViewPreferences()).toEqual(close);
    expect(JSON.parse(localStorage.getItem(VIEW_KEY)!)).toEqual({ ...saved, ...close });
    for (const change of [{ world_id: "mars:swarm:other" }, { layout_version: WORLD.layout_version + 1 }]) {
      expect(parse({ ...close, ...change })).toMatchObject({ mode: "overview", viewpoint: "reference", followAgentId: null });
    }
  });

  it("rejects an unknown camera preset instead of restoring unrecognized view state", () => {
    expect(parse({ viewpoint: "unknown_reference" })).toMatchObject({ mode: "overview", viewpoint: "reference", followAgentId: null, pose: null });
  });

  it("restores a selected agent and trims surrounding spaces without needing a stale pose", () => {
    expect(parse({})).toEqual(preferences);
    expect(parse({ followAgentId: "  comms  ", pose: null })).toEqual({ ...preferences, pose: null });
    expect(parse({ followAgentId: "a".repeat(128) }).followAgentId).toHaveLength(128);
  });

  it.each([undefined, null, 7, {}, "", "   ", "a".repeat(129), "comms\n", "com\u0000ms", "com\u007fms", "com\u0085ms"])(
    "exits follow for invalid agent identity %j, keeping only a usable camera pose", (followAgentId) => {
      expect(parse({ followAgentId })).toEqual({ ...preferences, mode: "orbit", followAgentId: null });
      expect(parse({ followAgentId, pose: null })).toEqual({ ...preferences, mode: "overview", followAgentId: null, pose: null });
    },
  );

  it.each(["overview", "outpost", "orbit", "player"] as const)("clears follow identity when restoring %s mode", (mode) => {
    expect(parse({ mode })).toEqual({ ...preferences, mode, followAgentId: null });
  });

  it("still reads older camera preferences that have no follow field", () => {
    const { followAgentId: _identity, ...legacy } = saved;
    expect(parseViewPreferences(JSON.stringify({ ...legacy, mode: "orbit" }))).toEqual({ ...preferences, mode: "orbit", followAgentId: null });
  });

  it("does not restore follow selection from another world or layout", () => {
    for (const change of [{ world_id: "mars:swarm:other" }, { layout_version: WORLD.layout_version + 1 }]) {
      expect(parse(change)).toMatchObject({ mode: "overview", followAgentId: null, pose: null });
    }
    expect(parseViewPreferences("broken JSON")).toMatchObject({ mode: "overview", followAgentId: null, pose: null });
  });

  it("stores only view fields and leaves other world and authoritative state untouched", () => {
    const otherKey = "jarvis.mars:swarm:other.view.v1";
    const authorityKey = "jarvis.mars:ordinary.journeys";
    const journey = JSON.stringify({ agent_id: "comms", position: [1, 2, 3], status: "moving" });
    localStorage.setItem(otherKey, "other view");
    localStorage.setItem(authorityKey, journey);
    saveViewPreferences({ ...preferences, followAgentId: "  comms  ",
      pose: { ...pose, agentPosition: [1, 2, 3] }, agentPosition: [1, 2, 3], journey,
    } as ViewPreferences);
    expect(JSON.parse(localStorage.getItem(VIEW_KEY)!)).toEqual(saved);
    expect(readViewPreferences()).toEqual(preferences);
    expect(localStorage.getItem(otherKey)).toBe("other view");
    expect(localStorage.getItem(authorityKey)).toBe(journey);
  });

  it("also validates follow identity before saving", () => {
    saveViewPreferences({ ...preferences, followAgentId: "" });
    expect(readViewPreferences()).toEqual({ ...preferences, mode: "orbit", followAgentId: null });
    saveViewPreferences({ ...preferences, mode: "player" });
    expect(readViewPreferences()).toEqual({ ...preferences, mode: "player", followAgentId: null });
  });

  it("keeps controls usable when local storage is denied", () => {
    vi.stubGlobal("localStorage", {
      getItem() { throw new Error("storage denied"); },
      setItem() { throw new Error("storage denied"); },
    });
    expect(readViewPreferences()).toMatchObject({ mode: "overview", followAgentId: null, pose: null });
    expect(() => saveViewPreferences(preferences)).not.toThrow();
  });
});
