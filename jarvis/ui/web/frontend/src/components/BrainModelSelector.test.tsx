import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(),
}));

import { BrainModelSelector } from "./BrainModelSelector";
import { openExternalUrl } from "@/lib/openExternal";

const MODELS = {
  provider: "gemini",
  current_model: "gemini-3-flash-preview",
  models: [
    { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
    { id: "gemini-3-flash-preview", label: "Gemini 3 Flash" },
  ],
  source: "live",
  fetched_at: 1,
};

function mockFetch(saveBody?: Record<string, unknown>) {
  return vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/models")) return { ok: true, json: async () => MODELS };
    if (init?.method === "PUT") {
      return {
        ok: true,
        json: async () =>
          saveBody ?? {
            ok: true,
            provider: "gemini",
            model: "x",
            persisted: true,
            applied_live: true,
            restart_required: false,
            probe: { status: "ok", detail: "", latency_ms: 30, integration_ok: true },
          },
      };
    }
    return { ok: true, json: async () => ({}) };
  });
}

async function openDropdown() {
  const trigger = (await screen.findByLabelText(
    "apikeys_model.model_label",
  )) as HTMLElement;
  fireEvent.click(trigger);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BrainModelSelector", () => {
  it("opens a dropdown listing the provider's models", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<BrainModelSelector providerId="gemini" currentModel="gemini-3-flash-preview" />);
    await openDropdown();
    expect(await screen.findByText("gemini-3.1-pro-preview")).toBeTruthy();
    expect(screen.getByText("gemini-3-flash-preview")).toBeTruthy();
  });

  it("filters the list via the search box", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<BrainModelSelector providerId="gemini" currentModel="" />);
    await openDropdown();
    const search = (await screen.findByLabelText(
      "apikeys_model.search_placeholder",
    )) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "pro" } });
    expect(await screen.findByText("gemini-3.1-pro-preview")).toBeTruthy();
    expect(screen.queryByText("gemini-3-flash-preview")).toBeNull();
  });

  it("matches a hyphenated id when the user types spaces (gpt 5.5)", async () => {
    // OpenRouter ids use hyphens (openai/gpt-5.5); users naturally type
    // "gpt 5.5". A naive substring search found nothing — the 2026-06-28 report.
    const OR_MODELS = {
      provider: "openrouter",
      current_model: "",
      models: [
        { id: "openai/gpt-5.5", label: "OpenAI: GPT-5.5" },
        { id: "openai/gpt-5.5-pro", label: "OpenAI: GPT-5.5 Pro" },
        { id: "z-ai/glm-5.2", label: "Z.AI: GLM 5.2" },
      ],
      source: "live",
      fetched_at: 1,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/models")) return { ok: true, json: async () => OR_MODELS };
        return { ok: true, json: async () => ({}) };
      }),
    );
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    const search = (await screen.findByLabelText(
      "apikeys_model.search_placeholder",
    )) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "gpt 5.5" } });
    expect(await screen.findByText("OpenAI: GPT-5.5")).toBeTruthy();
    // Unrelated model is filtered out.
    expect(screen.queryByText("Z.AI: GLM 5.2")).toBeNull();
  });

  it("matches with no separators at all (gpt5.5pro)", async () => {
    const OR_MODELS = {
      provider: "openrouter",
      current_model: "",
      models: [
        { id: "openai/gpt-5.5", label: "OpenAI: GPT-5.5" },
        { id: "openai/gpt-5.5-pro", label: "OpenAI: GPT-5.5 Pro" },
      ],
      source: "live",
      fetched_at: 1,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/models")) return { ok: true, json: async () => OR_MODELS };
        return { ok: true, json: async () => ({}) };
      }),
    );
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    const search = (await screen.findByLabelText(
      "apikeys_model.search_placeholder",
    )) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "gpt5.5pro" } });
    expect(await screen.findByText("OpenAI: GPT-5.5 Pro")).toBeTruthy();
    expect(screen.queryByText("OpenAI: GPT-5.5")).toBeNull();
  });

  it("shows ALL search hits, not just the first 80", async () => {
    // A search must reach EVERY matching model — the old 80-row display cap hid
    // the rest of a large OpenRouter catalog (user report: "can't search all
    // models"). 120 models sharing a token; searching surfaces all of them.
    const many = Array.from({ length: 120 }, (_, i) => ({
      id: `vendor/model-${i}`,
      label: `Vendor Model ${i}`,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/models"))
          return {
            ok: true,
            json: async () => ({
              provider: "openrouter",
              current_model: "",
              models: many,
              source: "live",
              fetched_at: 1,
            }),
          };
        return { ok: true, json: async () => ({}) };
      }),
    );
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    const search = (await screen.findByLabelText(
      "apikeys_model.search_placeholder",
    )) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "vendor" } });
    // The 119th match (well past the old 80-row cap) must be rendered.
    expect(await screen.findByText("Vendor Model 119")).toBeTruthy();
  });

  it("shows the COMPLETE catalog on open, without typing (no display cap)", async () => {
    // User mandate: the whole catalog must be visible/scrollable on open, not a
    // truncated slice. 250 models; the 249th must render without any search.
    const many = Array.from({ length: 250 }, (_, i) => ({
      id: `vendor/model-${i}`,
      label: `Vendor Model ${i}`,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/models"))
          return {
            ok: true,
            json: async () => ({
              provider: "openrouter",
              current_model: "",
              models: many,
              source: "live",
              fetched_at: 1,
            }),
          };
        return { ok: true, json: async () => ({}) };
      }),
    );
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    expect(await screen.findByText("Vendor Model 249")).toBeTruthy();
  });

  it("links each OpenRouter model to its page via the external-link bridge", async () => {
    const OR_MODELS = {
      provider: "openrouter",
      current_model: "",
      models: [{ id: "openai/gpt-5.5", label: "OpenAI: GPT-5.5" }],
      source: "live",
      fetched_at: 1,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/models")) return { ok: true, json: async () => OR_MODELS };
        return { ok: true, json: async () => ({}) };
      }),
    );
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    const link = await screen.findByLabelText("apikeys_model.open_on_provider");
    fireEvent.click(link);
    expect(openExternalUrl).toHaveBeenCalledWith("https://openrouter.ai/openai/gpt-5.5");
  });

  it("shows no provider link for a direct provider (no per-model page)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<BrainModelSelector providerId="gemini" currentModel="" />);
    await openDropdown();
    await screen.findByText("gemini-3.1-pro-preview");
    expect(screen.queryByLabelText("apikeys_model.open_on_provider")).toBeNull();
  });

  it("saves a model picked from the list via PUT", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<BrainModelSelector providerId="gemini" currentModel="" />);
    await openDropdown();
    fireEvent.click(await screen.findByText("gemini-3.1-pro-preview"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(String(put![0])).toContain("/api/providers/gemini/model");
      expect(JSON.parse((put![1] as RequestInit).body as string).model).toBe(
        "gemini-3.1-pro-preview",
      );
    });
  });

  it("saves a custom model id that is not in the list", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<BrainModelSelector providerId="openrouter" currentModel="" />);
    await openDropdown();
    const search = (await screen.findByLabelText(
      "apikeys_model.search_placeholder",
    )) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "acme/experimental-9" } });
    fireEvent.click(screen.getByTestId("use-custom-row"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(JSON.parse((put![1] as RequestInit).body as string).model).toBe(
        "acme/experimental-9",
      );
    });
  });

  it("shows an honest probe status chip after saving", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        ok: true,
        provider: "gemini",
        model: "bogus",
        persisted: true,
        applied_live: false,
        restart_required: false,
        probe: {
          status: "model_unavailable",
          detail: "404 model",
          latency_ms: 5,
          integration_ok: true,
        },
      }),
    );
    render(<BrainModelSelector providerId="gemini" currentModel="" />);
    await openDropdown();
    fireEvent.click(await screen.findByText("gemini-3.1-pro-preview"));
    expect(await screen.findByText("apikeys_test.status_model_unavailable")).toBeTruthy();
  });

  it("renders safely when the response has no models array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ provider: "gemini" }) }),
    );
    render(<BrainModelSelector providerId="gemini" currentModel="" />);
    await openDropdown();
    // No crash — the search box is present so custom entry still works.
    expect(
      await screen.findByLabelText("apikeys_model.search_placeholder"),
    ).toBeTruthy();
  });
});
