/**
 * Component tests for FeedbackView.
 *
 * The Feedback section is Discord-only: a single button forwards the user to the
 * #bug-reports channel via {@link openExternalUrl}. There is no in-app form.
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

    // A button that forwards to Discord must be present.
    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    // The CTA button text references opening Discord.
    expect(screen.getByText(/open discord/i)).toBeTruthy();
  });

  it("does NOT render the old in-app form (Discord-only)", () => {
    render(<FeedbackView />);
    // No title/description inputs anymore.
    expect(screen.queryByLabelText(/title/i)).toBeNull();
    expect(screen.queryByLabelText(/description/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /submit/i })).toBeNull();
  });

  it("clicking the button opens the Discord invite via the external-open bridge", () => {
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);
    fireEvent.click(screen.getByText(/open discord/i));

    expect(openSpy).toHaveBeenCalledTimes(1);
    // Must forward to a discord.gg invite link.
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringContaining("discord.gg/"),
    );
  });
});
