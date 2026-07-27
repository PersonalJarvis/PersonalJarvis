import { describe, expect, it } from "vitest";

import { OffscreenBuffer } from "./offscreenBuffer";

describe("OffscreenBuffer", () => {
  it("hands back exactly what it was given, in order, once", () => {
    const buffer = new OffscreenBuffer();
    buffer.push("one ");
    buffer.push("two ");
    buffer.push("three");

    expect(buffer.drain()).toBe("one two three");
    // Drained means drained — a second look must not repaint the same output.
    expect(buffer.drain()).toBe("");
    expect(buffer.pending).toBe(0);
  });

  it("ignores empty chunks", () => {
    const buffer = new OffscreenBuffer();
    buffer.push("");
    expect(buffer.pending).toBe(0);
    expect(buffer.drain()).toBe("");
  });

  it("drops the OLDEST output when a hidden pane floods", () => {
    // A terminal UI repaints continuously, so the newest bytes are the screen.
    // Keeping the oldest would replay a screen that no longer exists.
    const buffer = new OffscreenBuffer(20);
    buffer.push("STALE");
    buffer.push("0123456789");
    buffer.push("abcdefghij");
    buffer.push("NEWEST");

    const held = buffer.drain();
    expect(held.endsWith("NEWEST")).toBe(true);
    expect(held).not.toContain("STALE");
    expect(buffer.dropped).toBeGreaterThan(0);
  });

  it("never drops the newest chunk, however large", () => {
    const buffer = new OffscreenBuffer(10);
    const huge = "x".repeat(500);
    buffer.push("old");
    buffer.push(huge);

    expect(buffer.drain()).toBe(huge);
  });

  it("stays bounded across a long flood", () => {
    const limit = 1024;
    const buffer = new OffscreenBuffer(limit);
    for (let i = 0; i < 500; i += 1) buffer.push("y".repeat(64));

    // One chunk of slack: the newest is always kept whole.
    expect(buffer.pending).toBeLessThanOrEqual(limit + 64);
  });
});
