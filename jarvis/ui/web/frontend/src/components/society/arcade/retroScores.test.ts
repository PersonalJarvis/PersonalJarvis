import { beforeEach, describe, expect, it } from "vitest";
import { bestKey, clearBestMemory, readBest, saveBest, type ScoreStorage } from "./retroScores";

/** A plain in-memory Storage stand-in. */
function memoryStorage(): ScoreStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => { data.set(k, v); } };
}

/** Storage that throws on every call, like blocked site data. */
const throwing: ScoreStorage = {
  getItem: () => { throw new Error("blocked"); },
  setItem: () => { throw new Error("blocked"); },
};

beforeEach(() => clearBestMemory());

describe("retro best scores", () => {
  it("uses the documented key per game", () => {
    expect(bestKey("neon-snake")).toBe("jarvis.office.arcade.neon-snake.best");
  });

  it("starts at zero and keeps only a higher score", () => {
    const store = memoryStorage();
    expect(readBest("neon-snake", store)).toBe(0);
    expect(saveBest("neon-snake", 120, store)).toBe(true);
    expect(store.data.get(bestKey("neon-snake"))).toBe("120");
    expect(saveBest("neon-snake", 80, store)).toBe(false);
    expect(saveBest("neon-snake", 120, store)).toBe(false);
    expect(readBest("neon-snake", store)).toBe(120);
    expect(readBest("block-drop", store)).toBe(0);
  });

  it("ignores garbage in storage and in scores", () => {
    const store = memoryStorage();
    store.data.set(bestKey("road-hopper"), "not a number");
    expect(readBest("road-hopper", store)).toBe(0);
    expect(saveBest("road-hopper", Number.NaN, store)).toBe(false);
    expect(saveBest("road-hopper", -5, store)).toBe(false);
    expect(saveBest("road-hopper", 41.7, store)).toBe(true);
    expect(readBest("road-hopper", store)).toBe(41);
  });

  it("works with storage that throws or is missing, for the rest of the session", () => {
    expect(readBest("city-defense", throwing)).toBe(0);
    expect(saveBest("city-defense", 300, throwing)).toBe(true);
    expect(readBest("city-defense", throwing)).toBe(300);
    expect(readBest("city-defense", null)).toBe(300);
    expect(saveBest("city-defense", 200, null)).toBe(false);
  });
});
