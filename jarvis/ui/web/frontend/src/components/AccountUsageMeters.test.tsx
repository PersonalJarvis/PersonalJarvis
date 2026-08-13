/**
 * The meters that say how much of a subscription's plan is gone.
 *
 * What these pin down is the difference between a useful meter and a harmful
 * one. A percentage next to a seat is a number the user makes a decision on, so
 * the failures worth a test are the ones where it is confidently wrong or
 * quietly missing:
 *
 * * a reading that came off disk must never be presented as a live one;
 * * a per-model weekly budget must be drawn NEXT TO the overall week, because
 *   55% overall beside 99% on one model is exactly when "plenty left" is wrong;
 * * a limit this build has no name for must still be drawn, not hidden;
 * * a seat with no login must not repeat what the row above already says.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AccountUsageMeters } from "./AccountUsageMeters";
import type { AccountUsage, UsageWindow } from "@/lib/agentAccountsApi";

afterEach(cleanup);

const NOW = Date.UTC(2026, 7, 13, 11, 0, 0);

function window_(overrides: Partial<UsageWindow>): UsageWindow {
  return {
    kind: "weekly",
    percent: 40,
    severity: "normal",
    resets_at: null,
    window_minutes: 10080,
    scope_label: null,
    raw_label: null,
    ...overrides,
  };
}

function usage(overrides: Partial<AccountUsage> = {}): AccountUsage {
  return {
    account_id: "claude:seat",
    platform: "claude",
    status: "ok",
    windows: [window_({})],
    source: "live",
    as_of: NOW / 1000,
    message: "",
    plan: null,
    ...overrides,
  };
}

describe("AccountUsageMeters", () => {
  it("draws every window, including a scoped one beside the overall week", () => {
    render(
      <AccountUsageMeters
        now={NOW}
        usage={usage({
          plan: "Max 20x",
          windows: [
            window_({ kind: "session", percent: 2, window_minutes: 300 }),
            window_({ kind: "weekly", percent: 55 }),
            window_({
              kind: "weekly_scoped",
              percent: 99,
              severity: "critical",
              scope_label: "Fable",
            }),
          ],
        })}
      />,
    );

    const bars = screen.getAllByRole("progressbar");
    expect(bars).toHaveLength(3);
    expect(bars.map((bar) => bar.getAttribute("aria-valuenow"))).toEqual(["2", "55", "99"]);
    // The scoped budget names WHAT it is scoped to — a second unlabelled
    // "Weekly" bar at 99% would read as a contradiction of the first.
    expect(screen.getByText(/Fable/)).toBeTruthy();
    expect(screen.getByText("Max 20x")).toBeTruthy();
  });

  it("says a disk reading is not a live one", () => {
    render(
      <AccountUsageMeters
        now={NOW}
        // Four hours old — a perfectly ordinary age for an idle seat, and
        // exactly the case where showing it as "live" would mislead.
        usage={usage({ source: "cached", as_of: NOW / 1000 - 4 * 3600 })}
      />,
    );
    expect(screen.queryByText("live")).toBeNull();
    expect(screen.getByText(/4 h/)).toBeTruthy();
  });

  it("counts down to the reset and never counts up from a passed one", () => {
    const { rerender } = render(
      <AccountUsageMeters
        now={NOW}
        usage={usage({
          windows: [window_({ resets_at: new Date(NOW + 2.5 * 3600 * 1000).toISOString() })],
        })}
      />,
    );
    expect(screen.getByText(/2 h 30 min/)).toBeTruthy();

    // A reset in the past means the window already rolled over. "in -3 h" is
    // nonsense, so the countdown disappears rather than turning negative.
    rerender(
      <AccountUsageMeters
        now={NOW}
        usage={usage({
          windows: [window_({ resets_at: new Date(NOW - 3 * 3600 * 1000).toISOString() })],
        })}
      />,
    );
    expect(screen.queryByText(/-/)).toBeNull();
  });

  it("still draws a limit this build has no name for", () => {
    render(
      <AccountUsageMeters
        now={NOW}
        usage={usage({
          windows: [
            window_({ kind: "other", percent: 62, window_minutes: 4320, raw_label: "burst" }),
          ],
        })}
      />,
    );
    // Hiding an unrecognised limit is the one failure that costs the user the
    // information they came for — the limit throttling them right now.
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect(screen.getByText(/3 d/)).toBeTruthy();
  });

  it("stays silent for a seat that is not signed in", () => {
    const { container } = render(
      <AccountUsageMeters now={NOW} usage={usage({ status: "signed_out", windows: [] })} />,
    );
    // The row above already says "Not signed in yet" in plain words.
    expect(container.textContent).toBe("");
  });

  it("explains an unreadable seat instead of drawing an empty bar", () => {
    render(
      <AccountUsageMeters now={NOW} usage={usage({ status: "unavailable", windows: [] })} />,
    );
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
    expect(screen.getByText(/not readable/i)).toBeTruthy();
  });

  it("renders nothing at all while the reading is still in flight", () => {
    const { container } = render(<AccountUsageMeters now={NOW} usage={undefined} />);
    expect(container.textContent).toBe("");
  });
});
