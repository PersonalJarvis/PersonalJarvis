/**
 * The badge that stopped saying "live".
 *
 * Every case here is about a pane the OLD badge described identically: socket
 * up, process alive, `live` — while one was mid-refactor, one had been finished
 * for twenty minutes, and one was sitting on an unanswered question. Telling
 * those apart is the entire feature, so the tests are about the distinctions
 * rather than about the markup.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PaneActivityPill, durationLabel } from "./PaneActivityPill";

/** The clock the tests read, so nothing here races the wall. */
const NOW_MS = 1_700_000_000_000;
const NOW_S = NOW_MS / 1000;

function pill(props: Parameters<typeof PaneActivityPill>[0]) {
  render(<PaneActivityPill {...props} now={NOW_MS} />);
  return screen.getByTestId("pane-activity");
}

describe("what the badge says", () => {
  it("says the agent is working while its screen moves", () => {
    expect(pill({ status: "live", activity: "working" }).textContent).toBe(
      "working",
    );
  });

  it("says done for a pane that was given a job and has stopped", () => {
    expect(
      pill({ status: "live", activity: "waiting", tasked: true }).textContent,
    ).toBe("done");
  });

  it("says idle — never done — for a pane nobody has instructed", () => {
    // The SAME still screen as the case above. Calling it "done" would invent a
    // job for a terminal that was never given one, and read as "your work is
    // ready" on a pane that has done none.
    expect(
      pill({ status: "live", activity: "waiting", tasked: false }).textContent,
    ).toBe("idle");
  });

  it("calls out a pane that is waiting for an answer", () => {
    expect(pill({ status: "live", activity: "asking" }).textContent).toBe(
      "needs you",
    );
  });

  it("reports an agent that could not be started", () => {
    expect(pill({ status: "live", activity: "failed" }).textContent).toBe(
      "failed",
    );
  });

  it("reports an agent whose process is gone", () => {
    expect(pill({ status: "live", activity: "exited" }).textContent).toBe(
      "exited",
    );
  });
});

describe("when the pipe is the news", () => {
  it("reports a socket that is still connecting", () => {
    // Whatever the last known activity was, it describes a pane this viewer is
    // not connected to yet — the connection is what the user can act on.
    expect(
      pill({ status: "connecting", activity: "working" }).textContent,
    ).toBe("starting");
  });

  it("reports a broken pane, and says what broke in the tooltip", () => {
    const badge = pill({ status: "error", detail: "Socket closed." });
    expect(badge.textContent).toBe("error");
    expect(badge.getAttribute("title")).toContain("Socket closed.");
  });

  it("falls back to the old badge for a pane with no reading", () => {
    // A plain terminal runs no agent, so it has no job to be in the middle of,
    // and the backend answers with no activity at all. The honest thing left to
    // say is that the pipe is up.
    expect(pill({ status: "live", activity: "" }).textContent).toBe("live");
  });
});

describe("how long it has been that way", () => {
  it("puts the duration in the tooltip, never in the badge", () => {
    // The badge sits in a narrow column beside a call-sign; a number that
    // changes every second there is movement without information.
    const badge = pill({
      status: "live",
      activity: "waiting",
      tasked: true,
      since: NOW_S - 180,
    });
    expect(badge.textContent).toBe("done");
    expect(badge.getAttribute("title")).toContain("For 3 min.");
  });

  it("says nothing about timing when the backend does not know", () => {
    const badge = pill({ status: "live", activity: "working", since: 0 });
    expect(badge.getAttribute("title")).not.toContain("For");
  });

  it("reads as a duration rather than a moment", () => {
    expect(durationLabel(NOW_S - 12, NOW_MS)).toBe("12s");
    expect(durationLabel(NOW_S - 200, NOW_MS)).toBe("3 min");
    expect(durationLabel(NOW_S - 3600, NOW_MS)).toBe("1 hour");
    // A stamp from the future is a clock disagreement, not negative time.
    expect(durationLabel(NOW_S + 500, NOW_MS)).toBe("0s");
  });
});

describe("what a screen reader hears", () => {
  it("carries the word and the sentence behind it", () => {
    const badge = pill({ status: "live", activity: "working" });
    expect(badge.getAttribute("aria-label")).toContain("working");
    expect(badge.getAttribute("aria-label")).toContain("screen is still");
  });
});
