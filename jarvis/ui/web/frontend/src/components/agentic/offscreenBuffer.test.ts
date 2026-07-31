import { describe, expect, it } from "vitest";

import { OFFSCREEN_LIMIT_CHARS, OffscreenBuffer } from "./offscreenBuffer";

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

  it("comes due once it has held its oldest chunk long enough", () => {
    // The backstop for a pane that is wrong about being watched. Size alone
    // could not provide it: a CLI that prints one line and waits never reaches
    // the limit, so its pane held that line for as long as the agent stayed
    // quiet — a terminal that had visibly stopped while the work ran on.
    const buffer = new OffscreenBuffer(OFFSCREEN_LIMIT_CHARS, 1500);
    const start = 10_000;

    expect(buffer.stale(start)).toBe(false);
    expect(buffer.dueIn(start)).toBeNull();

    buffer.push("the agent said something", start);
    expect(buffer.stale(start + 1499)).toBe(false);
    expect(buffer.dueIn(start + 500)).toBe(1000);
    expect(buffer.stale(start + 1500)).toBe(true);

    // Draining answers it, exactly like `full` — otherwise a pane past its
    // deadline would write on every chunk from then on rather than coalescing.
    buffer.drain();
    expect(buffer.stale(start + 5000)).toBe(false);
    expect(buffer.dueIn(start + 5000)).toBeNull();
  });

  it("measures the deadline from the OLDEST chunk, not the newest", () => {
    // A pane fed a steady trickle would otherwise push its own deadline back
    // with every chunk and never come due — which is the busiest pane, and so
    // exactly the one whose freeze a user notices.
    const buffer = new OffscreenBuffer(OFFSCREEN_LIMIT_CHARS, 1500);
    const start = 10_000;

    buffer.push("first", start);
    for (let at = start + 200; at <= start + 1400; at += 200) {
      buffer.push("more", at);
    }

    expect(buffer.stale(start + 1500)).toBe(true);
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
