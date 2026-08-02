/**
 * The flat/space preference and the honesty rule attached to it.
 *
 * The rule that matters is the last one: a machine that cannot give a WebGL
 * context must never be handed a 3D scene. It would paint nothing, and a map
 * that silently paints nothing is worse than a flat map that works.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_GRAPH_DIMENSION,
  GRAPH_DIMENSION_KEY,
  parseGraphDimension,
  readGraphDimension,
  resolveGraphDimension,
  writeGraphDimension,
} from "@/lib/graphDimension";

beforeEach(() => {
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
});

afterEach(() => {
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
});

describe("parseGraphDimension", () => {
  it("accepts the two known values", () => {
    expect(parseGraphDimension("2d")).toBe("2d");
    expect(parseGraphDimension("3d")).toBe("3d");
  });

  it("treats anything else as unset", () => {
    expect(parseGraphDimension(null)).toBeNull();
    expect(parseGraphDimension("")).toBeNull();
    expect(parseGraphDimension("4d")).toBeNull();
    expect(parseGraphDimension("3D")).toBeNull();
  });
});

describe("readGraphDimension", () => {
  it("falls back to flat when nothing is stored", () => {
    expect(readGraphDimension()).toBe(DEFAULT_GRAPH_DIMENSION);
    expect(DEFAULT_GRAPH_DIMENSION).toBe("2d");
  });

  it("round-trips a stored preference", () => {
    writeGraphDimension("3d");
    expect(window.localStorage.getItem(GRAPH_DIMENSION_KEY)).toBe("3d");
    expect(readGraphDimension()).toBe("3d");
  });

  it("ignores a corrupted value instead of failing", () => {
    window.localStorage.setItem(GRAPH_DIMENSION_KEY, "hologram");
    expect(readGraphDimension()).toBe("2d");
  });
});

describe("resolveGraphDimension", () => {
  it("honours 3D when the machine can render it", () => {
    expect(resolveGraphDimension("3d", true)).toBe("3d");
  });

  it("degrades 3D to the flat map when WebGL is missing", () => {
    expect(resolveGraphDimension("3d", false)).toBe("2d");
  });

  it("leaves the flat map alone either way", () => {
    expect(resolveGraphDimension("2d", true)).toBe("2d");
    expect(resolveGraphDimension("2d", false)).toBe("2d");
  });
});
