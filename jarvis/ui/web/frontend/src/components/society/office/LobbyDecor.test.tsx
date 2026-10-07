/**
 * The lobby brand wall's canvas face writes in the bundled interface face.
 *
 * The bundle ships Inter as "Inter Variable"; a canvas font naming plain
 * "Inter" silently drew the wordmark in the system fallback, so the
 * "PERSONAL JARVIS" sign looked off-brand. A recording 2D context captures
 * every font the face sets and every line of text it draws.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { LOBBY_RENDERERS } from "./LobbyDecor";

const fonts: string[] = [];
const texts: string[] = [];

/** A 2D context that records `font` and `fillText`, and accepts every other call. */
function recordingContext(): CanvasRenderingContext2D {
  const gradient = { addColorStop: () => undefined };
  const state: Record<string, unknown> = {};
  return new Proxy(state, {
    get(target, key) {
      if (key === "measureText") return (text: string) => ({ width: text.length * 60 });
      if (key === "createLinearGradient" || key === "createRadialGradient") return () => gradient;
      if (key === "fillText") return (text: string) => { texts.push(text); };
      if (key in target) return target[key as string];
      return () => undefined;
    },
    set(target, key, value) {
      if (key === "font") fonts.push(String(value));
      target[key as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const realGetContext = HTMLCanvasElement.prototype.getContext;

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return recordingContext();
  } as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

afterAll(() => {
  HTMLCanvasElement.prototype.getContext = realGetContext;
});

describe("lobby brand wall", () => {
  it("draws the wordmark and strapline in the bundled Inter face", () => {
    const wall = LOBBY_RENDERERS.brandWall() as ReactElement;
    // Calling the component builds its materials, which draw the canvas face.
    (wall.type as () => ReactElement)();
    expect(texts).toEqual(expect.arrayContaining(["PERSONAL JARVIS", "AGENT HEADQUARTERS"]));
    expect(fonts.length).toBeGreaterThan(0);
    for (const font of fonts) expect(font).toContain("'Inter Variable'");
  });
});
