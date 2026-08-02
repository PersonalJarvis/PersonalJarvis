import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScreenContextGroup } from "./ScreenContextGroup";

/**
 * Screen Context is a ONE-SWITCH card (maintainer mandate 2026-08-02). These
 * tests pin that contract from the outside: the only control is the switch, a
 * blocker is reported honestly while the feature is on, and nothing on the card
 * asks the user to write privacy rules by hand.
 */

const SETTINGS = { enabled: true };

function status(available: boolean, enabled = true) {
  return {
    enabled,
    available,
    blocked_reason: available ? null : "No vision-capable provider is configured.",
    blocked_reasons: available
      ? []
      : ["No vision-capable provider is configured."],
    monitor_count: 2,
  };
}

function response(body: unknown) {
  return { ok: true, json: async () => body };
}

afterEach(() => vi.restoreAllMocks());

describe("ScreenContextGroup", () => {
  it("reports the real blocker while the feature is switched on", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        response(url.endsWith("/settings") ? SETTINGS : status(false)),
      ),
    );

    render(<ScreenContextGroup />);

    await waitFor(() =>
      expect(
        screen.getByText("No vision-capable provider is configured."),
      ).toBeTruthy(),
    );
  });

  it("offers exactly one control and no privacy-rule editors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        response(url.endsWith("/settings") ? SETTINGS : status(true)),
      ),
    );

    const { container } = render(<ScreenContextGroup />);

    await waitFor(() => expect(screen.getByText(/Ready on 2/)).toBeTruthy());
    expect(container.querySelectorAll("textarea")).toHaveLength(0);
    expect(container.querySelectorAll("input")).toHaveLength(0);
    // The old card shipped Save / Test / Discard buttons plus three more
    // switches next to this one.
    const buttons = Array.from(container.querySelectorAll("button"));
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("role")).toBe("switch");
  });

  it("writes only the enabled flag when the switch is flipped", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/settings") && init?.method === "PUT") {
        return response({ ok: true, changed: ["enabled"] });
      }
      if (url.endsWith("/settings")) return response(SETTINGS);
      if (url.endsWith("/status")) return response(status(true));
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<ScreenContextGroup />);
    const toggle = await waitFor(() => {
      const found = container.querySelector("button[role=switch]");
      if (!found) throw new Error("switch not rendered yet");
      return found;
    });
    fireEvent.click(toggle);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({
        enabled: false,
      });
    });
  });
});
