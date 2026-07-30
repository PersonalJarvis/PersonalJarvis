/**
 * Component tests for the header bell.
 *
 * The feature is not the list, it is the four things around it, and each one has
 * an obvious wrong implementation:
 *
 * 1. The badge is the whole point of a bell — it has to appear with a count and
 *    go away once the panel has been looked at, or nothing on screen ever says
 *    a terminal stopped.
 * 2. "Jump to pane" has to hand the ENTRY back, not a name. An entry from
 *    another workspace needs its workspace id, and a caller that only receives
 *    "T3" cannot switch tab to the right one.
 * 3. Discarding must clear on screen immediately. It is a delete button, and one
 *    that waits for a round-trip reads as a broken one.
 * 4. An empty bell and a switched-off bell must not look the same, or "nothing
 *    is arriving" has no visible cause.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const pushToast = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ pushToast, assistantName: "Jarvis" }),
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchPaneNotifications: vi.fn(),
  markPaneNotificationsRead: vi.fn(),
  clearPaneNotifications: vi.fn(),
}));

import { PaneNotifications, ago } from "./PaneNotifications";
import {
  clearPaneNotifications,
  fetchPaneNotifications,
  markPaneNotificationsRead,
  type PaneNotification,
  type PaneNotificationsState,
} from "@/lib/agenticIdeApi";

function entry(over: Partial<PaneNotification> = {}): PaneNotification {
  return {
    id: "n1",
    kind: "completed",
    workspace_id: "ide_1",
    workspace: "Personal Jarvis",
    pane_key: "t1",
    pane: "T1",
    agent: "claude",
    display_name: "Claude Code",
    title: "Finished and waiting at its prompt",
    detail: "rewrite the resume store",
    created_at: Date.now() / 1000 - 180,
    read: false,
    ...over,
  };
}

function state(entries: PaneNotification[], over: Partial<PaneNotificationsState> = {}) {
  return {
    enabled: true,
    unread: entries.filter((e) => !e.read).length,
    notifications: entries,
    ...over,
  };
}

const fetchMock = vi.mocked(fetchPaneNotifications);
const readMock = vi.mocked(markPaneNotificationsRead);
const clearMock = vi.mocked(clearPaneNotifications);

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockResolvedValue(state([]));
  readMock.mockResolvedValue(0);
  clearMock.mockResolvedValue(undefined);
});

afterEach(cleanup);

async function open() {
  const bell = await screen.findByTestId("pane-notifications-bell");
  fireEvent.click(bell);
  return screen.findByTestId("pane-notifications-panel");
}

describe("the bell", () => {
  it("shows a count when terminals have stopped and nobody has looked", async () => {
    fetchMock.mockResolvedValue(state([entry(), entry({ id: "n2", pane: "T2" })]));
    render(<PaneNotifications onJump={vi.fn()} />);

    const badge = await screen.findByTestId("pane-notifications-count");
    expect(badge.textContent).toBe("2");
  });

  it("stays quiet with nothing to report", async () => {
    render(<PaneNotifications onJump={vi.fn()} />);

    await screen.findByTestId("pane-notifications-bell");
    expect(screen.queryByTestId("pane-notifications-count")).toBeNull();
  });

  it("clears the count once the panel has been opened", async () => {
    fetchMock.mockResolvedValue(state([entry()]));
    render(<PaneNotifications onJump={vi.fn()} />);
    await screen.findByTestId("pane-notifications-count");

    await open();

    await waitFor(() => expect(readMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId("pane-notifications-count")).toBeNull(),
    );
  });
});

describe("the panel", () => {
  it("says which pane and what happened, not just that something did", async () => {
    fetchMock.mockResolvedValue(state([entry()]));
    render(<PaneNotifications onJump={vi.fn()} />);

    const panel = await open();

    expect(panel.textContent).toContain("T1");
    expect(panel.textContent).toContain("Finished and waiting at its prompt");
    // The workspace travels too: a call-sign counts from T1 in every tab.
    expect(panel.textContent).toContain("Personal Jarvis");
  });

  it("hands the whole entry to the jump, so another workspace can be reached", async () => {
    const onJump = vi.fn();
    const other = entry({ id: "n9", workspace_id: "ide_other", pane: "T4" });
    fetchMock.mockResolvedValue(state([other]));
    render(<PaneNotifications onJump={onJump} />);
    await open();

    fireEvent.click(await screen.findByTestId("pane-notification-jump"));

    expect(onJump).toHaveBeenCalledWith(expect.objectContaining({
      workspace_id: "ide_other",
      pane: "T4",
    }));
    // And it gets out of the way — the jump is about seeing the grid.
    await waitFor(() =>
      expect(screen.queryByTestId("pane-notifications-panel")).toBeNull(),
    );
  });

  it("removes a discarded entry immediately rather than after the round-trip", async () => {
    let resolveDelete: () => void = () => {};
    clearMock.mockImplementation(
      () => new Promise<void>((resolve) => (resolveDelete = resolve)),
    );
    fetchMock.mockResolvedValue(state([entry(), entry({ id: "n2", pane: "T2" })]));
    render(<PaneNotifications onJump={vi.fn()} />);
    await open();
    expect(screen.getAllByTestId("pane-notification")).toHaveLength(2);

    fireEvent.click(screen.getAllByTestId("pane-notification-discard")[0]);

    await waitFor(() =>
      expect(screen.getAllByTestId("pane-notification")).toHaveLength(1),
    );
    resolveDelete();
  });

  it("empties on 'discard all'", async () => {
    fetchMock.mockResolvedValue(state([entry(), entry({ id: "n2" })]));
    render(<PaneNotifications onJump={vi.fn()} />);
    await open();

    fireEvent.click(screen.getByTestId("pane-notifications-clear"));

    await waitFor(() => expect(screen.getByTestId("pane-notifications-empty")).toBeTruthy());
    expect(clearMock).toHaveBeenCalledWith(undefined);
  });

  it("distinguishes 'nothing happened' from 'collection is switched off'", async () => {
    fetchMock.mockResolvedValue(state([], { enabled: false }));
    render(<PaneNotifications onJump={vi.fn()} />);

    const panel = await open();

    expect(panel.textContent).toContain("switched off");
  });

  it("labels each kind so a failure is not read as a success", async () => {
    fetchMock.mockResolvedValue(
      state([
        entry({ id: "a", kind: "needs_input" }),
        entry({ id: "b", kind: "failed" }),
      ]),
    );
    render(<PaneNotifications onJump={vi.fn()} />);

    const panel = await open();

    expect(panel.textContent).toContain("NEEDS INPUT");
    expect(panel.textContent).toContain("FAILED");
  });
});

describe("relative time", () => {
  it("reads in whatever unit keeps it short", () => {
    const now = 1_000_000_000_000;
    const at = now / 1000;
    expect(ago(at - 12, now)).toBe("12s ago");
    expect(ago(at - 180, now)).toBe("3m ago");
    expect(ago(at - 7200, now)).toBe("2h ago");
    expect(ago(at - 172_800, now)).toBe("2d ago");
  });

  it("ages an entry filed long after the workspace was opened", async () => {
    /*
     * The bug this pins read "0s ago" on every entry in the panel, however old,
     * and it survived the test above because that one calls `ago` directly.
     * The component used to freeze its idea of "now" when it MOUNTED — which is
     * when the workspace opens — and refresh it only every 30 s, and only while
     * the panel was open. So a notification filed at any point after the
     * workspace opened was measured against a moment before it existed, and the
     * clamp in `ago` turned that negative age into zero.
     *
     * Hence the shape here: mount first, let an hour pass, and only then does
     * anything arrive.
     */
    // Only the clock is faked — the polling and the queries keep their real
    // timers, so this stays an ordinary render-and-click test.
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2026-07-30T12:00:00Z"));
      fetchMock.mockResolvedValue(state([]));
      render(<PaneNotifications onJump={vi.fn()} />);
      await screen.findByTestId("pane-notifications-bell");

      // An hour of the workspace sitting open, and then a pane finishes.
      vi.setSystemTime(new Date("2026-07-30T13:00:00Z"));
      fetchMock.mockResolvedValue(state([entry({ created_at: Date.now() / 1000 - 600 })]));

      const panel = await open();

      expect(panel.textContent).toContain("10m ago");
      expect(panel.textContent).not.toContain("0s ago");
    } finally {
      vi.useRealTimers();
    }
  });
});
