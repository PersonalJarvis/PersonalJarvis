import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key (assert keys exactly).
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

// The toast store is only used for side effects here; stub it to a no-op.
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { WikiProviderCard } from "./WikiProviderCard";

// Object-shape fixture per the canonical backend: `available` rows carry
// {provider, models[], kind, ready}; `resolved` states what the next run uses.
const INITIAL = {
  provider: "",
  model: "",
  available: [
    {
      provider: "gemini",
      models: ["gemini-3-flash-preview", "gemini-3.1-pro-preview"],
      kind: "api",
      ready: true,
    },
    { provider: "openai", models: ["gpt-5.5"], kind: "api", ready: false },
    { provider: "codex", models: [], kind: "agent", ready: true },
  ],
  resolved: { provider: "gemini", model: "gemini-3-flash-preview", ready: true },
  brain_primary: "gemini",
};

// Per-provider live catalog served to BrainModelSelector (the model picker).
const CATALOGS: Record<string, { id: string; label: string }[]> = {
  gemini: [
    { id: "gemini-3-flash-preview", label: "Gemini 3 Flash" },
    { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
  ],
  codex: [
    { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
    { id: "gpt-5.5", label: "GPT-5.5" },
  ],
};

/**
 * URL-routing fetch stub: settings GET/PUT + the per-provider model catalog.
 * Returns the mock so tests can inspect recorded calls.
 */
function stubFetch(overrides?: { state?: typeof INITIAL; putBody?: object }) {
  const state = overrides?.state ?? INITIAL;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/settings/wiki-provider") {
      if (init?.method === "PUT") {
        const sent = JSON.parse(init.body as string);
        return {
          ok: true,
          json: async () =>
            overrides?.putBody ?? {
              ...state,
              provider: sent.provider,
              model: sent.model,
              persisted: true,
              applied_live: true,
              restart_required: false,
            },
        };
      }
      return { ok: true, json: async () => state };
    }
    const catalog = url.match(/^\/api\/providers\/([^/]+)\/models/);
    if (catalog) {
      const id = decodeURIComponent(catalog[1]);
      return {
        ok: true,
        json: async () => ({
          provider: id,
          current_model: "",
          models: CATALOGS[id] ?? [],
          source: "curated",
          fetched_at: 0,
          selects: "model",
        }),
      };
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("WikiProviderCard", () => {
  it("renders the Wiki tier label and the provider select once loaded", async () => {
    stubFetch();
    render(<WikiProviderCard />);

    expect(screen.getByText("wiki_provider.tier_label")).toBeDefined();
    await waitFor(() =>
      expect(screen.getByLabelText("wiki_provider.provider_label")).toBeDefined(),
    );
  });

  it("renders gracefully when the GET load fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<WikiProviderCard />);

    // The tier header stays; the body shows the load-error line, no crash, no
    // provider select.
    expect(screen.getByText("wiki_provider.tier_label")).toBeDefined();
    await waitFor(() => expect(screen.getByText("wiki_provider.load_error")).toBeDefined());
    expect(screen.queryByLabelText("wiki_provider.provider_label")).toBeNull();
  });

  it("shows the how-it-works explainer with all three steps", async () => {
    stubFetch();
    render(<WikiProviderCard />);

    await screen.findByText("wiki_provider.how_title");
    expect(screen.getByText("wiki_provider.how_step1")).toBeDefined();
    expect(screen.getByText("wiki_provider.how_step2")).toBeDefined();
    expect(screen.getByText("wiki_provider.how_step3")).toBeDefined();
    expect(screen.getByText("wiki_provider.how_model_note")).toBeDefined();
  });

  it("shows what the next run actually uses (resolved line)", async () => {
    stubFetch();
    render(<WikiProviderCard />);

    await screen.findByText("wiki_provider.resolved_label");
    expect(screen.getByText(/gemini · gemini-3-flash-preview/)).toBeDefined();
  });

  it("warns when the resolved provider has no credentials", async () => {
    stubFetch({
      state: {
        ...INITIAL,
        provider: "openai",
        resolved: { provider: "openai", model: "gpt-5.5", ready: false },
      },
    });
    render(<WikiProviderCard />);

    await screen.findByText("wiki_provider.resolved_fallback_warning");
  });

  it("labels agent providers and keyless providers in the dropdown", async () => {
    stubFetch();
    render(<WikiProviderCard />);

    await screen.findByLabelText("wiki_provider.provider_label");
    expect(
      screen.getByText("codex — wiki_provider.option_agent_suffix"),
    ).toBeDefined();
    expect(
      screen.getByText("openai (wiki_provider.option_no_key)"),
    ).toBeDefined();
  });

  it('renders NO model dropdown while the provider is "same as brain"', async () => {
    stubFetch();
    render(<WikiProviderCard />);

    await screen.findByLabelText("wiki_provider.provider_label");
    // Static text instead of a pointless one-option dropdown.
    expect(screen.getByText("wiki_provider.model_follow_primary")).toBeDefined();
    expect(screen.queryByLabelText("apikeys_model.model_label")).toBeNull();
  });

  it("opens the catalog picker for a concrete provider and saves the pick", async () => {
    const fetchMock = stubFetch();
    render(<WikiProviderCard />);

    const providerSelect = (await screen.findByLabelText(
      "wiki_provider.provider_label",
    )) as HTMLSelectElement;

    // Picking codex mounts the shared model picker fed by codex's catalog.
    fireEvent.change(providerSelect, { target: { value: "codex" } });
    const trigger = await screen.findByLabelText("apikeys_model.model_label");
    fireEvent.click(trigger);

    // Codex's real lineup appears (the old tier-default list was empty).
    const row = await screen.findByText("GPT-5.6 Sol");
    fireEvent.click(row);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/wiki-provider");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        provider: "codex",
        model: "gpt-5.6-sol",
      });
    });
  });

  it("sends a PUT with the chosen provider when Apply is clicked", async () => {
    const fetchMock = stubFetch();
    render(<WikiProviderCard />);

    const providerSelect = (await screen.findByLabelText(
      "wiki_provider.provider_label",
    )) as HTMLSelectElement;

    fireEvent.change(providerSelect, { target: { value: "gemini" } });
    fireEvent.click(screen.getByRole("button", { name: "wiki_provider.apply" }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/wiki-provider");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        provider: "gemini",
        model: "",
      });
    });
  });

  it("offers a reset back to the cheap default when a model is pinned", async () => {
    const fetchMock = stubFetch({
      state: { ...INITIAL, provider: "gemini", model: "gemini-3.1-pro-preview" },
    });
    render(<WikiProviderCard />);

    const reset = await screen.findByText("wiki_provider.model_reset");
    fireEvent.click(reset);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        provider: "gemini",
        model: "",
      });
    });
  });
});
