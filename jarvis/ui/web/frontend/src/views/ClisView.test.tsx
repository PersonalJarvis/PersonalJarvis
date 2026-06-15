/**
 * Component tests for the ClisView catalog list.
 *
 * Contract: the row title is the human-readable `display_name`
 * ("Google Cloud CLI"), NOT the technical slug ("gcloud") — users could not
 * tell what "gcloud" was. The slug stays visible as a secondary monospace
 * command badge, and the subtitle carries only the description (no
 * "display_name · description" prefix, which would duplicate the title).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ClisView } from "@/views/ClisView";

// ViewHeader pulls in ChatsView, which subscribes to a WS client; null keeps
// that effect a deterministic no-op in jsdom (same pattern as ProfileView.test).
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const LIST_OK = {
  clis: [
    {
      name: "gcloud",
      display_name: "Google Cloud CLI",
      category: "cloud",
      icon: "",
      description: "Google Cloud Platform CLI for Compute, Storage, IAM, GKE, Cloud Run.",
      status: "connected",
      installed: true,
      connected: true,
      version: "527.0.0",
      auth_mode: "oauth_cli",
      is_custom: false,
      last_used_at: null,
      usage_count_7d: 0,
      error: null,
    },
  ],
  total: 1,
  connected: 1,
  installed: 1,
  categories: ["cloud"],
};

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/clis")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => LIST_OK,
        text: async () => JSON.stringify(LIST_OK),
      } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ClisView row title", () => {
  it("renders the display name as the single row title and the slug as a badge", async () => {
    installFetchMock();
    renderWithClient(<ClisView />);

    // Exactly one element with the bare display name: the row title. If the
    // subtitle still rendered "display_name · description", this would either
    // fail to match (old layout: slug title) or match twice (duplicate).
    const title = await screen.findByText("Google Cloud CLI");
    expect(title).toBeTruthy();

    // The command slug must stay visible (it is what you type in a terminal).
    expect(screen.getByText("gcloud")).toBeTruthy();
  });
});
