/**
 * Pure helpers behind the Explore view's visual language.
 *
 * Two things carry meaning here and both are derived from the data, never
 * decorative: how big a node is (how often a topic comes up) and how bright
 * it is (how recently). The time bar next to each topic is the same idea in
 * list form — where in your history this topic actually lived.
 */
import { describe, expect, it } from "vitest";

import {
  corpusSpan,
  nodeRadius,
  recencyTint,
  spanBar,
  type SpannedEntity,
} from "@/lib/entityGraph";

const entity = (first: string, last: string, mentions = 1): SpannedEntity => ({
  first_seen: first,
  last_seen: last,
  mentions,
});

describe("corpusSpan", () => {
  it("brackets the earliest and latest moment across all topics", () => {
    const span = corpusSpan([
      entity("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
      entity("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
    ]);

    expect(span.start).toBe(Date.parse("2026-01-01T00:00:00Z"));
    expect(span.end).toBe(Date.parse("2026-04-01T00:00:00Z"));
  });

  it("returns an empty span for no topics rather than NaN", () => {
    expect(corpusSpan([])).toEqual({ start: 0, end: 0 });
  });

  it("ignores unparseable timestamps instead of poisoning the span", () => {
    const span = corpusSpan([
      entity("not-a-date", "also-not"),
      entity("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
    ]);

    expect(span.start).toBe(Date.parse("2026-01-01T00:00:00Z"));
    expect(span.end).toBe(Date.parse("2026-02-01T00:00:00Z"));
  });
});

describe("spanBar", () => {
  const span = {
    start: Date.parse("2026-01-01T00:00:00Z"),
    end: Date.parse("2026-05-01T00:00:00Z"),
  };

  it("places a topic that ran the whole time across the full width", () => {
    const bar = spanBar(entity("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"), span);

    expect(bar.offset).toBeCloseTo(0);
    expect(bar.width).toBeCloseTo(1);
  });

  it("places a late, short-lived topic at the right edge", () => {
    const bar = spanBar(entity("2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"), span);

    expect(bar.offset).toBeGreaterThan(0.7);
    expect(bar.width).toBeLessThan(0.3);
  });

  it("gives a single-moment topic a visible minimum width", () => {
    const bar = spanBar(entity("2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z"), span);

    // A zero-width bar would render as nothing at all — the one case where
    // being literal makes the data invisible.
    expect(bar.width).toBeGreaterThan(0);
    expect(bar.offset + bar.width).toBeLessThanOrEqual(1);
  });

  it("falls back to the full width when the corpus has no time span", () => {
    const bar = spanBar(entity("2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z"), {
      start: 0,
      end: 0,
    });

    expect(bar).toEqual({ offset: 0, width: 1 });
  });
});

describe("nodeRadius", () => {
  it("grows with mentions but keeps the rarest node visible", () => {
    expect(nodeRadius(1, 100)).toBeGreaterThan(0);
    expect(nodeRadius(100, 100)).toBeGreaterThan(nodeRadius(1, 100));
  });

  it("compresses the range so one dominant topic cannot swamp the canvas", () => {
    // Linear scaling would make a 132-mention node 132x the area of a 1.
    const ratio = nodeRadius(132, 132) / nodeRadius(1, 132);
    expect(ratio).toBeLessThan(6);
  });

  it("does not divide by zero when every topic is equally rare", () => {
    expect(Number.isFinite(nodeRadius(1, 1))).toBe(true);
  });
});

describe("recencyTint", () => {
  const span = {
    start: Date.parse("2026-01-01T00:00:00Z"),
    end: Date.parse("2026-05-01T00:00:00Z"),
  };

  it("returns a hex colour for both ends of the span", () => {
    expect(recencyTint("2026-01-01T00:00:00Z", span)).toMatch(/^#[0-9a-f]{6}$/);
    expect(recencyTint("2026-05-01T00:00:00Z", span)).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("brightens towards the present", () => {
    const old = recencyTint("2026-01-01T00:00:00Z", span);
    const fresh = recencyTint("2026-05-01T00:00:00Z", span);

    expect(old).not.toBe(fresh);
  });

  it("survives an unparseable timestamp without producing NaN colours", () => {
    expect(recencyTint("whenever", span)).toMatch(/^#[0-9a-f]{6}$/);
  });
});
