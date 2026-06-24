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

import { CuModelSelector } from "./CuModelSelector";

const MODELS = {
  provider: "gemini",
  current_model: "gemini-3.5-flash",
  models: [
    { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
    { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
  ],
  source: "live",
  fetched_at: 1,
};

function mockFetch(cuModel = "", opts?: { putRestart?: boolean }) {
  return vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/cu-model") && (!init || init.method === "GET" || !init.method)) {
      // GET /cu-model
      if (!init || init.method !== "PUT") {
        return {
          ok: true,
          json: async () => ({
            provider: "gemini",
            cu_model: cuModel,
            effective_model: cuModel || "gemini-3.5-flash",
            uses_main: !cuModel,
          }),
        };
      }
    }
    if (u.includes("/cu-model") && init?.method === "PUT") {
      const sent = JSON.parse((init.body as string) || "{}");
      return {
        ok: true,
        json: async () => ({
          ok: true,
          provider: "gemini",
          cu_model: sent.cu_model ?? "",
          effective_model: sent.cu_model || "gemini-3.5-flash",
          uses_main: !sent.cu_model,
          persisted: true,
          restart_required: opts?.putRestart ?? false,
        }),
      };
    }
    if (u.includes("/models")) return { ok: true, json: async () => MODELS };
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CuModelSelector", () => {
  it("loads the current CU model and shows the 'using main' state when unpinned", async () => {
    vi.stubGlobal("fetch", mockFetch(""));
    render(<CuModelSelector providerId="gemini" />);
    expect(await screen.findByText("apikeys_cu_model.using_main")).toBeTruthy();
  });

  it("pins a CU model picked from the list via PUT /cu-model", async () => {
    const fetchMock = mockFetch("");
    vi.stubGlobal("fetch", fetchMock);
    render(<CuModelSelector providerId="gemini" />);
    const trigger = (await screen.findByLabelText("apikeys_model.model_label")) as HTMLElement;
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByText("gemini-3.1-pro-preview"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(String(put![0])).toContain("/api/providers/gemini/cu-model");
      expect(JSON.parse((put![1] as RequestInit).body as string).cu_model).toBe(
        "gemini-3.1-pro-preview",
      );
    });
  });

  it("clears back to the main model via the 'use main' button (PUT empty)", async () => {
    const fetchMock = mockFetch("gemini-3.1-pro-preview");
    vi.stubGlobal("fetch", fetchMock);
    render(<CuModelSelector providerId="gemini" />);
    // When pinned, the dedicated clear button is shown (testid disambiguates it
    // from the trigger placeholder, which shares the label text).
    const clearBtn = await screen.findByTestId("cu-use-main");
    fireEvent.click(clearBtn);
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === "PUT",
      );
      expect(put).toBeDefined();
      expect(JSON.parse((put![1] as RequestInit).body as string).cu_model).toBe("");
    });
  });
});
