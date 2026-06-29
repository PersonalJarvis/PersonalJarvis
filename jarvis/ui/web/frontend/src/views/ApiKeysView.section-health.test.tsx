/**
 * Component tests for the API-Keys section-health tab indicators.
 *
 * The segmented tab bar shows a corner dot per category: amber when the active
 * provider of that section still has to be set up ("needs_setup"), red when it
 * is set up but failing a live check ("error"). "ok" / "unknown" stay silent.
 * These tests pin that the dot + its plain-language tooltip render from the
 * /api/providers/section-health rollup.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { ApiKeysView } from "@/views/ApiKeysView";

interface RouteResult {
  status?: number;
  body: unknown;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const status = (init?.method ?? "GET").toUpperCase();
    void status;
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        const { status: code = 200, body } = routes[prefix]();
        return {
          ok: code >= 200 && code < 300,
          status: code,
          statusText: code >= 200 && code < 300 ? "OK" : "ERR",
          json: async () => body,
          text: async () => JSON.stringify(body),
        } as Response;
      }
    }
    // SubagentSection fetches /api/{codex,antigravity,claude}/status behind a
    // .catch — an unmatched route there is harmless. Everything the tabs need is
    // mocked below.
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

const SECTION_HEALTH = {
  sections: {
    brain: { status: "needs_setup", reason: "not_configured", detail: "OpenRouter: no key set" },
    tts: { status: "ok", reason: "ok", detail: "Gemini Flash: ok" },
    stt: { status: "error", reason: "bad_key", detail: "Groq STT: key invalid" },
    subagents: { status: "unknown", reason: "unknown", detail: "" },
    advanced: { status: "unknown", reason: "unknown", detail: "" },
  },
  checked_at: 0,
  cached: false,
};

function baseRoutes(overrides: Record<string, () => RouteResult> = {}) {
  return {
    "/api/providers/section-health": () => ({ body: SECTION_HEALTH }),
    "/api/providers": () => ({ body: { providers: [] } }),
    "/api/openclaw/status": () => ({ body: { mapping: [], brain_primary: "" } }),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView — section-health tab indicators", () => {
  it("marks a not-set-up tab amber ('Setup needed')", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Brain.*Setup needed/i })).toBeTruthy(),
    );
  });

  it("marks a broken tab red ('Not working') with the cause in the tooltip", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    const sttTab = await waitFor(() =>
      screen.getByRole("tab", { name: /Voice Input.*Not working/i }),
    );
    expect(sttTab.getAttribute("title")).toMatch(/key invalid/i);
  });

  it("leaves a healthy tab without any indicator", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    // The "Voice Output" tab is ok → no "Setup needed" / "Not working" in its name.
    await waitFor(() => screen.getByRole("tab", { name: /Brain/i }));
    const ttsTab = screen.getByRole("tab", { name: /^Voice Output$/i });
    expect(ttsTab.getAttribute("title")).toBeNull();
  });
});
