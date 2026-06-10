/**
 * Component tests for TelephonySetupView — the dedicated telephony "setup" page
 * reached via the "Setup script" button on the API-Keys telephony section.
 *
 * Design (2026-06-09): the heavy setup scripts + a step-by-step guide moved off
 * the compact embedded telephony section onto this page. These tests pin that
 * the guide steps and the scripts render, and that the "back" link returns to
 * the API-Keys view (store.activeSection === "apikeys").
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { TelephonySetupView } from "@/views/TelephonyView";
import { useEventStore } from "@/store/events";

interface RouteResult {
  status?: number;
  body: unknown;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        const { status = 200, body } = routes[prefix]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => body,
          text: async () => JSON.stringify(body),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn(async () => undefined) },
  });
}

const STATUS = {
  available: true,
  configured: true,
  enabled: true,
  account_sid_masked: "AC••••••cdef",
  phone_number: "+4930123456789",
  public_base_url: "https://jarvis.example.com",
  webhook_url: "https://jarvis.example.com/api/telephony/voice",
  auth_token_set: true,
  twilio_reachable: true,
  twilio_error: null,
  tts_provider: "gemini-flash-tts",
  tts_voice: "Charon",
  active_calls: 0,
  max_call_seconds: 600,
};

const SCRIPTS = {
  scripts: [
    {
      name: "Cloudflared tunnel",
      path: "scripts/telephony-tunnel.ps1",
      description: "Expose the local FastAPI port over a public HTTPS tunnel.",
      command: "pwsh scripts/telephony-tunnel.ps1 -Port 8765",
    },
  ],
};

function routes(): Record<string, () => RouteResult> {
  return {
    "/api/telephony/status": () => ({ body: STATUS }),
    "/api/telephony/scripts": () => ({ body: SCRIPTS }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TelephonySetupView", () => {
  it("renders the step-by-step guide and the setup scripts", async () => {
    installFetchMock(routes());
    render(<TelephonySetupView />);

    // A guide step (English default locale).
    expect(screen.getByText("Create a Twilio account")).toBeTruthy();
    // The setup script surfaces from /api/telephony/scripts.
    await waitFor(() =>
      expect(screen.getByText("Cloudflared tunnel")).toBeTruthy(),
    );
    // The live webhook URL is surfaced for copy/paste into Twilio.
    expect(
      screen.getByText("https://jarvis.example.com/api/telephony/voice"),
    ).toBeTruthy();
  });

  it("returns to the API-Keys view via the back link", async () => {
    installFetchMock(routes());
    useEventStore.getState().setActiveSection("telephony-setup");
    render(<TelephonySetupView />);

    const back = await waitFor(() =>
      screen.getByRole("button", { name: /Back to API Keys/i }),
    );
    fireEvent.click(back);
    expect(useEventStore.getState().activeSection).toBe("apikeys");
  });
});
