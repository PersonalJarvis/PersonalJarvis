import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScreenContextGroup } from "./ScreenContextGroup";

const SETTINGS = {
  enabled: true,
  denylist: [],
  sensitive_patterns: [],
  include_default_patterns: true,
  max_text_chars: 4000,
  ttl_s: 120,
  ocr_enabled: false,
};

function status(available: boolean) {
  return {
    enabled: true,
    available,
    blocked_reason: available ? null : "No vision-capable provider is configured.",
    blocked_reasons: available
      ? []
      : ["No vision-capable provider is configured."],
    monitor_count: 2,
    held_captures: 0,
    ttl_s: 120,
    components: {
      capture: { ready: true, detail: "" },
      indicator: { ready: true, detail: "" },
      vision: {
        ready: available,
        detail: available ? "" : "No vision-capable provider is configured.",
      },
      accessibility: { ready: true, detail: "" },
      ocr: { enabled: false, ready: false, detail: "Optional OCR is switched off." },
    },
  };
}

function response(body: unknown) {
  return { ok: true, json: async () => body };
}

afterEach(() => vi.restoreAllMocks());

describe("ScreenContextGroup", () => {
  it("shows the real blocker and labels empty-field text as examples", async () => {
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
    expect(screen.getAllByText(/only an example/i)).toHaveLength(2);
    expect(screen.getByText("Test one capture").closest("button")?.disabled).toBe(
      true,
    );
  });

  it("tests one capture and deletes only that handle", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/settings")) return response(SETTINGS);
      if (url.endsWith("/status")) return response(status(true));
      if (url.endsWith("/capture") && init?.method === "POST") {
        return response({
          status: "captured",
          id: "one-shot-handle",
          receipt: "monitor primary",
        });
      }
      if (url.endsWith("/one-shot-handle") && init?.method === "DELETE") {
        return response({ ok: true, discarded: 1 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ScreenContextGroup />);

    const button = await waitFor(() => screen.getByText("Test one capture"));
    fireEvent.click(button);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/screen-context/one-shot-handle",
        { method: "DELETE" },
      ),
    );
  });
});
