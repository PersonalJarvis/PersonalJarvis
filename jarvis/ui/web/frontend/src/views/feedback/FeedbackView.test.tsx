/**
 * Component tests for FeedbackView.
 *
 * The Feedback section is Discord-only: a single button forwards the user to the
 * #report-a-bug forum via {@link openExternalUrl}. A separate permanent invite
 * is available for people who have not joined the server yet.
 *
 * Verifies:
 *   - The Discord call-to-action (heading + button) renders.
 *   - Clicking the button opens the Discord invite via the external-open bridge
 *     (openExternalUrl), NOT a bare anchor — this is what makes it work inside
 *     the desktop WebView2 shell, which drops target="_blank".
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { FeedbackView } from "@/views/feedback/FeedbackView";
import * as openExternal from "@/lib/openExternal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FeedbackView", () => {
  it("renders the Discord call-to-action with a forward button", () => {
    render(<FeedbackView />);

    expect(
      screen.getByRole("button", { name: "Open #report-a-bug" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Join Discord first" }),
    ).toBeTruthy();
  });

  it("does NOT render the old in-app form (Discord-only)", () => {
    render(<FeedbackView />);
    // No title/description inputs anymore.
    expect(screen.queryByLabelText(/title/i)).toBeNull();
    expect(screen.queryByLabelText(/description/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /submit/i })).toBeNull();
  });

  it("opens the bug forum directly via the external-open bridge", () => {
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Open #report-a-bug" }),
    );

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      "https://discord.com/channels/1511102439066177656/1521522036709789736",
    );
  });

  it("keeps a permanent server invite for people who have not joined yet", () => {
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Join Discord first" }),
    );

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith("https://discord.gg/x7USduHxbc");
  });
});
