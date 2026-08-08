import { afterEach, describe, expect, it, vi } from "vitest";

import { RealtimeTransportBroker } from "./realtimeTransportBroker";
import {
  REALTIME_TRANSPORT_ISSUE_EVENT,
  type RealtimeTransportIssueDetail,
} from "./realtimeTransportIssue";

type PublishedIssue = RealtimeTransportIssueDetail["issue"];

/**
 * Drives a broker to its terminal state and reports what it published.
 *
 * Refusing the desktop capability on every attempt is the cheapest path to
 * `fail()`: six consecutive failures exhaust the retry budget.
 */
async function runUntilTerminal(
  requirement: boolean | null,
): Promise<{ issues: PublishedIssue[]; asked: number }> {
  const issues: PublishedIssue[] = [];
  vi.stubGlobal("window", {
    location: { protocol: "https:", host: "app.example" },
    dispatchEvent: (event: Event) => {
      if (event.type === REALTIME_TRANSPORT_ISSUE_EVENT) {
        issues.push((event as CustomEvent<RealtimeTransportIssueDetail>).detail.issue);
      }
      return true;
    },
  });

  let asked = 0;
  const pending: Array<() => void> = [];
  const broker = new RealtimeTransportBroker({
    mintTicket: vi.fn(async () => "ticket-1"),
    readDesktopCapability: () => "",
    readOfferRequirement: async () => {
      asked += 1;
      return requirement;
    },
    createSocket: () => {
      throw new Error("the broker must never reach the socket in this test");
    },
    createTransport: () => {
      throw new Error("the broker must never build a transport in this test");
    },
    schedule: (callback) => {
      pending.push(callback);
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
    cancelSchedule: () => undefined,
  });

  broker.start();
  while (pending.length > 0) pending.shift()?.();
  await vi.waitFor(() => expect(asked).toBe(1));
  // Let `publishFailure` resume past its await before the assertions read the
  // published list.
  await Promise.resolve();
  await Promise.resolve();
  return { issues, asked };
}

afterEach(() => vi.unstubAllGlobals());

describe("realtime transport issue gate", () => {
  it("withholds the accusation when no provider needs a browser offer", async () => {
    const { issues, asked } = await runUntilTerminal(false);
    expect(asked).toBe(1);
    expect(issues).toEqual([]);
  });

  it("publishes the blocker when a provider does need the offer", async () => {
    const { issues } = await runUntilTerminal(true);
    expect(issues).toEqual(["capability_missing"]);
  });

  it("publishes when the capability cannot be read, rather than hiding it", async () => {
    // `null` is "unknown", not "not required": a hidden real blocker is worse
    // than a noisy one.
    const { issues } = await runUntilTerminal(null);
    expect(issues).toEqual(["capability_missing"]);
  });
});
