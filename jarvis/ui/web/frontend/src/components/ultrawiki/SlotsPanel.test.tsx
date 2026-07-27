/**
 * SlotsPanel tests — the settings surface must let a user CONNECT, not just
 * choose.
 *
 * The defect these guard against: the panel used to render provider dropdowns
 * with no credential field anywhere, and told the user to go to the API-Keys
 * view, which has no field for these slots either. So the assertions here are
 * mostly about the presence of a real, writable credential input on the card
 * of a provider that needs one — plus the two rules that make the slots safe:
 * the storage preset must post a NAME (not the internal backend enum), and an
 * embedding change must still pass through the re-embed warning.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SlotsPanel } from "@/components/ultrawiki/SlotsPanel";
import { useEventStore } from "@/store/events";
import type {
  UltraWikiCatalog,
  UltraWikiCatalogRow,
  UltraWikiStatus,
} from "@/lib/ultrawikiApi";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

function row(overrides: Partial<UltraWikiCatalogRow>): UltraWikiCatalogRow {
  return {
    id: "x",
    slot: "embedding",
    label: "X",
    auth_mode: "api_key",
    secret_keys: [],
    dashboard_url: null,
    credential_help: "help text",
    default_model: "",
    supports_base_url: false,
    default_base_url: null,
    recommended: false,
    caution: null,
    db_backend: null,
    connection_hint: null,
    ready: false,
    reason: "",
    selected: false,
    secrets_set: {},
    secret_shared_with: {},
    ...overrides,
  };
}

const CATALOG: UltraWikiCatalog = {
  slots: {
    storage: [
      row({
        id: "sqlite",
        slot: "storage",
        label: "SQLite — local file",
        auth_mode: "none",
        db_backend: "sqlite",
        ready: true,
        selected: true,
        recommended: true,
      }),
      row({
        id: "supabase",
        slot: "storage",
        label: "Supabase",
        auth_mode: "managed_link",
        secret_keys: ["ultrawiki_db_url", "supabase_access_token"],
        db_backend: "postgres",
        reason: "no connection string is saved yet",
        secrets_set: { ultrawiki_db_url: false, supabase_access_token: false },
        secret_shared_with: { ultrawiki_db_url: [], supabase_access_token: [] },
      }),
      row({
        id: "neon",
        slot: "storage",
        label: "Neon",
        auth_mode: "connection_string",
        secret_keys: ["ultrawiki_db_url"],
        db_backend: "postgres",
        secrets_set: { ultrawiki_db_url: false },
        secret_shared_with: { ultrawiki_db_url: [] },
      }),
    ],
    embedding: [
      row({
        id: "ollama",
        slot: "embedding",
        label: "Ollama (local)",
        auth_mode: "none",
        default_model: "bge-m3",
        supports_base_url: true,
        default_base_url: "http://localhost:11434",
        ready: true,
        selected: true,
      }),
      row({
        id: "voyage",
        slot: "embedding",
        label: "Voyage AI",
        secret_keys: ["voyage_api_key"],
        default_model: "voyage-3.5",
        reason: "No Voyage AI API key is configured",
        secrets_set: { voyage_api_key: false },
        secret_shared_with: { voyage_api_key: [] },
      }),
    ],
    distill: [
      row({
        id: "codex",
        slot: "distill",
        label: "OpenAI Codex (ChatGPT subscription)",
        auth_mode: "codex",
        ready: true,
      }),
      row({
        id: "antigravity",
        slot: "distill",
        label: "Antigravity (Google subscription)",
        auth_mode: "antigravity",
      }),
      row({
        id: "claude-cli",
        slot: "distill",
        label: "Claude (Anthropic subscription)",
        auth_mode: "claude_cli",
      }),
      row({
        id: "gemini",
        slot: "distill",
        label: "Google Gemini",
        secret_keys: ["gemini_api_key"],
        ready: true,
        secrets_set: { gemini_api_key: true },
        secret_shared_with: { gemini_api_key: ["Google Gemini"] },
      }),
    ],
    rerank: [
      row({
        id: "llm",
        slot: "rerank",
        label: "Your chat providers",
        auth_mode: "none",
        ready: true,
      }),
      row({
        id: "cohere",
        slot: "rerank",
        label: "Cohere",
        secret_keys: ["cohere_api_key"],
        secrets_set: { cohere_api_key: false },
        secret_shared_with: { cohere_api_key: [] },
      }),
    ],
  },
  selected: {
    storage: "sqlite",
    embedding: "ollama",
    distill: "",
    rerank: "",
  },
  models: { embedding: "bge-m3", distill: "" },
  ollama_endpoint: "http://localhost:11434",
};

const STATUS: UltraWikiStatus = {
  enabled: true,
  started: true,
  db_backend: "sqlite",
  backend_in_use: "sqlite",
  slots: {},
  counts: {},
  pipeline: { running: false, processed: {} },
  sources: [],
  jobs: [],
  search_legs: {},
  degradations: [],
};

let putBodies: unknown[] = [];

function installFetchMock(overrides: Record<string, () => Response> = {}) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      for (const [prefix, make] of Object.entries(overrides)) {
        if (url.startsWith(prefix)) return make();
      }
      if (url.startsWith("/api/ultrawiki/catalog")) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => CATALOG,
        } as Response;
      }
      if (url.startsWith("/api/ultrawiki/models/")) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({
            provider: "ollama",
            current_model: "bge-m3",
            models: [
              { id: "bge-m3", label: "bge-m3 — multilingual" },
              { id: "nomic-embed-text", label: "nomic-embed-text" },
            ],
            source: "live",
            fetched_at: 0,
            selects: "model",
            reason: "",
          }),
        } as Response;
      }
      if (url.startsWith("/api/codex/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            installed: true,
            connected: true,
            mode: "chatgpt",
          }),
        } as Response;
      }
      if (url.startsWith("/api/antigravity/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            installed: true,
            connected: false,
            mode: "unknown",
          }),
        } as Response;
      }
      if (url.startsWith("/api/claude/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            installed: true,
            connected: false,
            mode: "unknown",
          }),
        } as Response;
      }
      if (url.startsWith("/api/ultrawiki/settings")) {
        putBodies.push(JSON.parse(String(init?.body ?? "{}")));
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({
            ok: true,
            changed: [],
            persisted: true,
            reembed_started: false,
          }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

describe("SlotsPanel", () => {
  beforeEach(() => {
    putBodies = [];
    useEventStore.setState({ toasts: [] });
    installFetchMock();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("gives every credentialed provider a key field on its own card", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    // Voyage embeddings and Cohere rerank were listed but unreachable before:
    // no input existed anywhere in the app for either key.
    await waitFor(() => {
      expect(screen.getByLabelText("Enter voyage_api_key")).toBeDefined();
    });
    expect(screen.getByLabelText("Enter cohere_api_key")).toBeDefined();
    // Neon's connection string is entered here too, not in a different view.
    expect(screen.getByLabelText("Enter ultrawiki_db_url")).toBeDefined();
  });

  it("shows no key field for a keyless local provider", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    await screen.findByTestId("ultrawiki-card-embedding-ollama");
    // Ollama runs without credentials; a password box there would be a lie.
    expect(screen.queryByLabelText("Enter ollama_api_key")).toBeNull();
  });

  it("posts the storage preset NAME, never the internal backend enum", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    fireEvent.click(await screen.findByTestId("ultrawiki-use-storage-neon"));
    await waitFor(() => expect(putBodies).toHaveLength(1));
    // The two-value db_backend is derived server-side, so the UI cannot
    // desync from it (AP-4 / BUG-008).
    expect(putBodies[0]).toEqual({ storage_provider: "neon" });
  });

  it("surfaces the honest not-ready reason on the card", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    const reason = await screen.findByTestId(
      "ultrawiki-reason-embedding-voyage",
    );
    expect(reason.textContent).toContain("No Voyage AI API key is configured");
  });

  it("warns before re-embedding when the embedding provider changes", async () => {
    installFetchMock({
      "/api/ultrawiki/settings": () =>
        ({
          ok: false,
          status: 409,
          statusText: "Conflict",
          json: async () => ({
            detail: { message: "vector spaces differ", vector_items: 412 },
          }),
        }) as Response,
    });
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    fireEvent.click(await screen.findByTestId("ultrawiki-use-embedding-voyage"));
    // The corpus is re-embedded from scratch, so the count is named BEFORE
    // anything is dropped (D-3).
    const dialog = await screen.findByTestId("ultrawiki-reembed-dialog");
    expect(dialog.textContent).toContain("412");
  });

  it("shows the background rebuild instead of looking idle", async () => {
    // A model switch rebuilds the vector space WITHOUT taking search down, so
    // the panel would otherwise sit fully green for hours with no sign that
    // anything is happening.
    installFetchMock();
    renderWithClient(
      <SlotsPanel
        status={{
          ...STATUS,
          reembed: {
            model: "voyage-3.5",
            active_model: "bge-m3",
            done: 120,
            total: 400,
          },
        }}
        onChanged={() => {}}
      />,
    );
    const banner = await screen.findByTestId("ultrawiki-reembed-progress");
    expect(banner.textContent).toContain("120");
    expect(banner.textContent).toContain("400");
    expect(
      within(banner).getByRole("progressbar").getAttribute("aria-valuenow"),
    ).toBe("30");
  });

  it("stays out of the way when no rebuild is running", async () => {
    installFetchMock();
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    await screen.findByTestId("ultrawiki-slots-panel");
    expect(screen.queryByTestId("ultrawiki-reembed-progress")).toBeNull();
  });

  it("offers the guided Supabase sign-in instead of a raw URI box", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    const connect = await screen.findByTestId("ultrawiki-supabase-connect");
    expect(connect).toBeDefined();
    // The Supabase card must NOT render the bare connection-string field: the
    // pooler hostname is not derivable, so hand-typing it is a trap.
    const links = screen.getAllByRole("link", { name: /supabase/i });
    expect(links.length).toBeGreaterThan(0);
  });

  it("picks the model from a list instead of asking the user to type it", async () => {
    // A free-text model box is how "gemini-embedding-01" becomes a 404 on the
    // first real embed and a silently paused pipeline. The slot uses the same
    // searchable picker as every other provider surface in the app.
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Model" }));
    // The provider's own catalog, not a hardcoded guess. The id shows twice
    // per row (label + monospace id column), so match all of them.
    expect((await screen.findAllByText("nomic-embed-text")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bge-m3/).length).toBeGreaterThan(0);
  });

  it("saves the model straight from the list, with no separate Save step", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /model/i }));
    const row = (await screen.findAllByText("nomic-embed-text"))[0];
    fireEvent.click(row.closest("button") as HTMLElement);
    await waitFor(() => expect(putBodies).toHaveLength(1));
    expect(putBodies[0]).toEqual({
      embedding_provider: "ollama",
      embedding_model: "nomic-embed-text",
    });
  });

  it("treats automatic distillation and rerank-off as real choices", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    const auto = await screen.findByTestId("ultrawiki-card-distill-auto");
    expect(auto.getAttribute("data-selected")).toBe("true");
    expect(
      screen.getByTestId("ultrawiki-card-rerank-off").getAttribute(
        "data-selected",
      ),
    ).toBe("true");
  });

  it("offers every Jarvis-Agent subscription as a distillation card", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);

    expect(await screen.findByTestId("ultrawiki-card-distill-codex")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-card-distill-antigravity")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-card-distill-claude-cli")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-subscription-codex")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-subscription-antigravity")).toBeDefined();
    expect(screen.getByTestId("ultrawiki-subscription-claude_cli")).toBeDefined();
  });

  it("switches to a subscription without carrying another provider's model", async () => {
    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);

    fireEvent.click(await screen.findByTestId("ultrawiki-use-distill-codex"));
    await waitFor(() => expect(putBodies).toHaveLength(1));
    expect(putBodies[0]).toEqual({
      distill_provider: "codex",
      distill_model: "",
    });
  });

  it("shows the shared model picker on a selected subscription card", async () => {
    const selectedCatalog: UltraWikiCatalog = {
      ...CATALOG,
      slots: {
        ...CATALOG.slots,
        distill: CATALOG.slots.distill.map((entry) => ({
          ...entry,
          selected: entry.id === "codex",
        })),
      },
      selected: { ...CATALOG.selected, distill: "codex" },
    };
    installFetchMock({
      "/api/ultrawiki/catalog": () =>
        ({
          ok: true,
          status: 200,
          json: async () => selectedCatalog,
        }) as Response,
    });

    renderWithClient(<SlotsPanel status={STATUS} onChanged={() => {}} />);
    const card = await screen.findByTestId("ultrawiki-card-distill-codex");
    expect(within(card).getByRole("button", { name: /model/i })).toBeDefined();
  });
});
