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

describe("what the badge shows", () => {
  it("spins while the screen moves", () => {
    // Motion, not a pulsing dot: a slow throb and a steady dot are the same
    // silhouette at 8 pixels, which put the whole busy/finished distinction
    // into a hue the eye had to compare against its neighbours.
    const badge = pill({ status: "live", activity: "working" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("spinner");
  });

  it("shows a still amber dot for a pane that was given a job and has stopped", () => {
    const badge = pill({ status: "live", activity: "waiting", worked: true });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("dot");
    expect(badge.className).toContain("text-amber-400");
  });

  it("hollows the dot — rather than recolouring it — for an unused pane", () => {
    // The SAME still screen as the case above. Calling it "done" would invent a
    // job for a terminal that was never given one, and read as "your work is
    // ready" on a pane that has done none. It is the same news minus one part,
    // so it is the same colour drawn empty.
    const badge = pill({ status: "live", activity: "waiting", worked: false });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("ring");
    expect(badge.className).toContain("text-amber-400/60");
  });

  it("keeps blue for the one state that wants something from you now", () => {
    // The only pane in the list that is neither busy nor simply finished, so
    // it is the only one worth spending a second colour on — and the only
    // still state allowed to wave: a beacon is a still dot with a radiating
    // halo, which reads as a notification rather than as the spinner's
    // grinding.
    const badge = pill({ status: "live", activity: "asking" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("beacon");
    expect(badge.className).toContain("text-sky-400");
  });

  it("shows an alert for an agent that could not be started", () => {
    const badge = pill({ status: "live", activity: "failed" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("alert");
  });

  it("shows a muted dot for an agent whose process is gone", () => {
    const badge = pill({ status: "live", activity: "exited" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("dot");
    expect(badge.className).toContain("text-muted-foreground");
  });
});

describe("when the pipe is the news", () => {
  it("shows a spinner for a socket that is still connecting", () => {
    // Whatever the last known activity was, it describes a pane this viewer is
    // not connected to yet — the connection is what the user can act on.
    const badge = pill({ status: "connecting", activity: "working" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("spinner");
  });

  it("reports a broken pane, and says what broke in the tooltip", () => {
    const badge = pill({ status: "error", detail: "Socket closed." });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("alert");
    expect(badge.getAttribute("title")).toContain("Socket closed.");
  });

  it("falls back to the old badge for a pane with no reading", () => {
    // A plain terminal runs no agent, so it has no job to be in the middle of,
    // and the backend answers with no activity at all. The honest thing left to
    // say is that the pipe is up — which is not news, so it is grey and hollow
    // rather than wearing the accent that means "something here is yours".
    const badge = pill({ status: "live", activity: "" });
    expect(badge.textContent).toBe("");
    expect(badge.getAttribute("data-icon")).toBe("ring");
    expect(badge.className).toContain("text-muted-foreground");
  });
});

describe("how long it has been that way", () => {
  it("puts the duration in the tooltip, never in the badge", () => {
    // The badge sits in a narrow column beside a call-sign; a number that
    // changes every second there is movement without information.
    const badge = pill({
      status: "live",
      activity: "waiting",
      worked: true,
      since: NOW_S - 180,
    });
    expect(badge.textContent).toBe("");
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
