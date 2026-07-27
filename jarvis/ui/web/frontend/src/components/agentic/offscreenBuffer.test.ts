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

  it("keeps the OLDEST output when a hidden pane floods", () => {
    // The front of the stream is what DREW the agent's interface; an Ink-based
    // TUI never repaints it on its own. Dropping it is what left panes showing
    // a spinner row over empty space (2026-07-27).
    const buffer = new OffscreenBuffer(20);
    buffer.push("FRAME");
    buffer.push("0123456789");
    buffer.push("abcdefghij");
    buffer.push("NEWEST");

    const held = buffer.drain();
    expect(held).toBe("FRAME0123456789abcdefghijNEWEST");
  });

  it("asks to be written out once it is holding its limit", () => {
    const buffer = new OffscreenBuffer(20);
    buffer.push("under the limit");
    expect(buffer.full).toBe(false);

    buffer.push("now well past it");
    expect(buffer.full).toBe(true);

    // Draining is what answers `full` — and it must clear the condition, or
    // the pane would write on every single chunk from then on.
    buffer.drain();
    expect(buffer.full).toBe(false);
  });

  it("stays bounded when its user drains on full", () => {
    // How the pane actually uses it: park, and write out whenever it is full.
    // Memory stays capped WITHOUT anything being discarded.
    const limit = 1024;
    const buffer = new OffscreenBuffer(limit);
    const written: string[] = [];
    let high = 0;

    for (let i = 0; i < 500; i += 1) {
      buffer.push("y".repeat(64));
      high = Math.max(high, buffer.pending);
      if (buffer.full) written.push(buffer.drain());
    }
    written.push(buffer.drain());

    expect(high).toBeLessThanOrEqual(limit + 64);
    expect(written.join("").length).toBe(500 * 64);
  });
});
