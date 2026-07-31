/**
 * ActivationWizard tests — the deliberate embedding choice (decision D-3).
 *
 * Pins that the wizard refuses to advance past the embedding step while the
 * selected backend is not ready (the honest reason is shown), because
 * activation would 409 on the backend anyway.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ActivationWizard } from "@/components/ultrawiki/ActivationWizard";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

const PROVIDERS_NONE_READY = {
  embedding: [
    {
      name: "ollama",
      ready: false,
      reason: "Ollama is not reachable at http://127.0.0.1:11434",
      default_model: "bge-m3",
    },
    {
      name: "gemini",
      ready: false,
      reason: "no GEMINI_API_KEY is configured",
      default_model: "gemini-embedding-001",
    },
  ],
  rerank: [],
  db_backends: [
    {
      name: "sqlite",
      ready: true,
      reason: "",
      detail: "Local file under the data directory.",
    },
    {
      name: "postgres",
      ready: false,
      secret_present: false,
      reason: "no 'ultrawiki_db_url' connection string is saved",
      detail: "PostgreSQL via connection string.",
    },
  ],
};

function installFetchMock(routes: Record<string, () => unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const prefix of Object.keys(routes)) {
      if (url.startsWith(prefix)) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => routes[prefix](),
        } as Response;
      }
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

describe("ActivationWizard — not-ready embedding backend", () => {
  it("blocks Next on the embedding step while the selected backend is not ready", async () => {
    installFetchMock({
      "/api/ultrawiki/providers": () => PROVIDERS_NONE_READY,
    });
    renderWithClient(
      <ActivationWizard onClose={vi.fn()} onActivated={vi.fn()} />,
    );

    // Step 1 (storage): SQLite is ready and preselected — Next advances.
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-wizard-storage-sqlite")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("ultrawiki-wizard-next"));

    // Step 2 (embedding): nothing selected yet → Next already blocked, and
    // the none-ready warning is on show.
    await waitFor(() => {
      expect(
        screen.getByTestId("ultrawiki-wizard-embedding-ollama"),
      ).toBeDefined();
    });
    expect(screen.getByTestId("ultrawiki-wizard-none-ready")).toBeDefined();
    const next = screen.getByTestId("ultrawiki-wizard-next") as HTMLButtonElement;
    expect(next.disabled).toBe(true);

    // Selecting a NOT-ready backend keeps Next blocked and shows the honest
    // per-card reason.
    fireEvent.click(screen.getByTestId("ultrawiki-wizard-embedding-ollama"));
    expect(
      screen.getByTestId("ultrawiki-wizard-embedding-ollama-reason").textContent,
    ).toContain("Ollama is not reachable");
    expect(
      (screen.getByTestId("ultrawiki-wizard-next") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("advances past the embedding step once a READY backend is selected", async () => {
    installFetchMock({
      "/api/ultrawiki/providers": () => ({
        ...PROVIDERS_NONE_READY,
        embedding: [
          {
            name: "gemini",
            ready: true,
            reason: "",
            default_model: "gemini-embedding-001",
          },
        ],
      }),
    });
    renderWithClient(
      <ActivationWizard onClose={vi.fn()} onActivated={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-wizard-storage-sqlite")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("ultrawiki-wizard-next"));

    await waitFor(() => {
      expect(
        screen.getByTestId("ultrawiki-wizard-embedding-gemini"),
      ).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("ultrawiki-wizard-embedding-gemini"));
    const next = screen.getByTestId("ultrawiki-wizard-next") as HTMLButtonElement;
    expect(next.disabled).toBe(false);

    fireEvent.click(next);
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-wizard-distill")).toBeDefined();
    });
  });
});
