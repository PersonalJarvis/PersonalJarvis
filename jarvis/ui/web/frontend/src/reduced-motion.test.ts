import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The app-wide `prefers-reduced-motion` floor must stay a floor.
 *
 * Individual components each guard their own animation, and they can be
 * removed, renamed or forgotten. The block at the end of index.css is what
 * makes the app honour the OS setting regardless — so these tests pin the two
 * properties that make it work at all: that it applies to everything, and that
 * it is written where nothing can override it.
 *
 * They also pin the three deliberate exceptions. Reduced motion is not "no
 * feedback": a spinner frozen mid-turn reads as a hang, a skeleton frozen at
 * the wrong point of its pulse can render invisible, and a ping ring frozen
 * mid-expand is a stray halo. Delete an exception and the app looks broken to
 * exactly the people the setting is meant to help.
 */

const CSS = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

/**
 * The last reduced-motion block in the file, and only that block.
 *
 * Slicing to the end of the file instead would drag in whatever happens to be
 * written below it. That is not hypothetical: the "solid ground" rules were
 * appended after this block, and every unrelated declaration in them then
 * counted as a floor rule that had forgotten its `!important`. Match the
 * braces so the helper answers the question the tests actually ask.
 */
function floorBlock(): string {
  const marker = "prefers-reduced-motion: reduce";
  const start = CSS.lastIndexOf(marker);
  expect(start).toBeGreaterThan(-1);
  const open = CSS.indexOf("{", start);
  expect(open).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = open; i < CSS.length; i += 1) {
    if (CSS[i] === "{") depth += 1;
    else if (CSS[i] === "}") {
      depth -= 1;
      if (depth === 0) return CSS.slice(start, i + 1);
    }
  }
  throw new Error("the reduced-motion block is not closed");
}

describe("the reduced-motion floor", () => {
  it("exists and applies to every element, including pseudo-elements", () => {
    const block = floorBlock();
    expect(block).toContain("*::before");
    expect(block).toContain("*::after");
  });

  it("collapses duration, delay and repeat for animations and transitions", () => {
    const block = floorBlock();
    for (const property of [
      "animation-duration",
      "animation-delay",
      "animation-iteration-count",
      "transition-duration",
      "transition-delay",
    ]) {
      expect(block).toContain(property);
    }
  });

  it("stops smooth scrolling, which is motion the animation rules do not reach", () => {
    expect(floorBlock()).toContain("scroll-behavior: auto");
  });

  it("wins over Tailwind utilities by being marked important", () => {
    const block = floorBlock();
    // Every rule in the floor carries !important; a rule without it would be
    // silently outranked by the utility class it is meant to tame.
    const declarations = block.match(/^\s+[a-z-]+:[^;]+;/gm) ?? [];
    expect(declarations.length).toBeGreaterThan(5);
    const unimportant = declarations.filter(
      (d) => !d.includes("!important") && !/opacity:/.test(d),
    );
    expect(unimportant).toEqual([]);
  });

  it("sits outside @layer, so layer ordering cannot demote it", () => {
    // Count braces from the start of the file to the block: inside a layer the
    // running depth would be 1 or more when the block opens.
    const start = CSS.lastIndexOf("@media (prefers-reduced-motion: reduce)");
    const before = CSS.slice(0, start);
    const depth =
      (before.match(/\{/g) ?? []).length - (before.match(/\}/g) ?? []).length;
    expect(depth).toBe(0);
  });
});

describe("the deliberate exceptions keep feedback visible", () => {
  it("keeps a spinner turning, slower, so it does not read as a hang", () => {
    const block = floorBlock();
    const spin = block.slice(block.indexOf(".animate-spin"));
    expect(spin).toMatch(/animation-iteration-count:\s*infinite\s*!important/);
    // Slower than the default 1s spin, or it defeats the point.
    const duration = spin.match(/animation-duration:\s*([\d.]+)s/);
    expect(duration).not.toBeNull();
    expect(Number(duration![1])).toBeGreaterThan(1);
  });

  it("turns a pulsing skeleton into a still, still-visible surface", () => {
    const block = floorBlock();
    const pulse = block.slice(block.indexOf(".animate-pulse"));
    expect(pulse).toMatch(/animation:\s*none\s*!important/);
    const opacity = pulse.match(/opacity:\s*([\d.]+)/);
    expect(opacity).not.toBeNull();
    // Dimmed, but never invisible — a blank area is not a loading state.
    expect(Number(opacity![1])).toBeGreaterThan(0.2);
  });

  it("turns an attention ring into a static halo rather than freezing it mid-expand", () => {
    const block = floorBlock();
    const ping = block.slice(block.indexOf(".animate-ping"));
    expect(ping).toMatch(/animation:\s*none\s*!important/);
    expect(ping).toMatch(/transform:\s*none\s*!important/);
  });
});
