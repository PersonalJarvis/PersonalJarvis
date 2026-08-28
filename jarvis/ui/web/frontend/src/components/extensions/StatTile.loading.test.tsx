import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatTile } from "./primitives";

/**
 * A tile that is still waiting must not show the number its caller invented.
 *
 * Every caller passes React Query's `isLoading`, which is true only while NO
 * answer has arrived, and passes a fallback like `totals?.cost_usd ?? 0` for
 * the value. Rendering that fallback — even faded — states a result: the Spend
 * section showed "$0.00", "0 tokens" and "Nothing recorded yet" for the whole
 * first read and was reported as a broken, empty section (2026-08-28). A
 * placeholder bar says "not yet" instead, which is the truth.
 */
describe("StatTile while it is waiting", () => {
  it("shows a placeholder instead of the caller's fallback number", () => {
    render(<StatTile label="Total spend" value="$0.00" hint="$0.00 per day" loading />);

    expect(screen.queryByText("$0.00")).toBeNull();
    expect(screen.queryByText("$0.00 per day")).toBeNull();
    // The label stays, so the row still reads as four named tiles.
    expect(screen.getByText("Total spend")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("shows the number as soon as one has arrived", () => {
    render(<StatTile label="Total spend" value="$17,002" hint="$563 per day" />);

    expect(screen.getByText("$17,002")).toBeTruthy();
    expect(screen.getByText("$563 per day")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps the placeholder visible against the surface it sits on", () => {
    // `bg-muted` is ~3% lightness from `bg-card`, so a skeleton painted with it
    // is invisible and the tile just looks blank — which is the same defect in
    // a different costume. A fraction of the foreground contrasts by
    // construction, in light mode and dark mode alike.
    const { container } = render(<StatTile label="Tokens" value="0" loading />);
    const bars = container.querySelectorAll(".animate-pulse");

    expect(bars.length).toBeGreaterThan(0);
    for (const bar of bars) {
      expect(bar.className).toContain("bg-foreground/");
      expect(bar.className).not.toContain("bg-muted");
    }
  });
});
