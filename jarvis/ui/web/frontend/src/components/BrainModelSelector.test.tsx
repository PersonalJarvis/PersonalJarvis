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

import { BrainModelSelector } from "./BrainModelSelector";

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
    fireEvent.change(search, { target: { value: "x-ai/grok-9-brandnew" } });
    fireEvent.click(screen.getByTestId("use-custom-row"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(JSON.parse((put![1] as RequestInit).body as string).model).toBe(
        "x-ai/grok-9-brandnew",
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
