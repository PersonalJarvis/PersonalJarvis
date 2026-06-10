import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Object-shape fixture per the canonical backend (Task 2): `available` is a list
// of { provider, models[] } objects, NOT bare strings.
const INITIAL = {
  provider: "",
  model: "",
  available: [
    { provider: "gemini", models: ["gemini-3-flash-preview", "gemini-3.1-pro-preview"] },
    { provider: "grok", models: ["grok-4.3"] },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WikiProviderCard", () => {
  it("renders the Wiki tier label and the provider select once loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => INITIAL }),
    );
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

  it("repopulates the model options from the chosen provider", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => INITIAL }),
    );
    render(<WikiProviderCard />);

    const providerSelect = (await screen.findByLabelText(
      "wiki_provider.provider_label",
    )) as HTMLSelectElement;
    const modelSelect = screen.getByLabelText(
      "wiki_provider.model_label",
    ) as HTMLSelectElement;

    // "Same as brain" provider → only the cheap-default model option.
    expect(within(modelSelect).queryByText("gemini-3-flash-preview")).toBeNull();

    // Picking gemini surfaces gemini's models; grok's stay hidden.
    fireEvent.change(providerSelect, { target: { value: "gemini" } });
    expect(within(modelSelect).getByText("gemini-3-flash-preview")).toBeDefined();
    expect(within(modelSelect).getByText("gemini-3.1-pro-preview")).toBeDefined();
    expect(within(modelSelect).queryByText("grok-4.3")).toBeNull();

    // Switching to grok swaps the model options.
    fireEvent.change(providerSelect, { target: { value: "grok" } });
    expect(within(modelSelect).getByText("grok-4.3")).toBeDefined();
    expect(within(modelSelect).queryByText("gemini-3-flash-preview")).toBeNull();
  });

  it("sends a PUT with the chosen provider and model when Apply is clicked", async () => {
    const applied = {
      provider: "gemini",
      model: "gemini-3.1-pro-preview",
      available: INITIAL.available,
    };
    const fetchMock = vi
      .fn()
      // GET on mount
      .mockResolvedValueOnce({ ok: true, json: async () => INITIAL })
      // PUT on Apply
      .mockResolvedValueOnce({ ok: true, json: async () => applied })
      // refetch() GET after a successful Apply
      .mockResolvedValueOnce({ ok: true, json: async () => applied });
    vi.stubGlobal("fetch", fetchMock);

    render(<WikiProviderCard />);
    const providerSelect = (await screen.findByLabelText(
      "wiki_provider.provider_label",
    )) as HTMLSelectElement;
    const modelSelect = screen.getByLabelText(
      "wiki_provider.model_label",
    ) as HTMLSelectElement;

    fireEvent.change(providerSelect, { target: { value: "gemini" } });
    fireEvent.change(modelSelect, { target: { value: "gemini-3.1-pro-preview" } });
    fireEvent.click(screen.getByRole("button", { name: "wiki_provider.apply" }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/wiki-provider");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        provider: "gemini",
        model: "gemini-3.1-pro-preview",
      });
    });
  });
});
