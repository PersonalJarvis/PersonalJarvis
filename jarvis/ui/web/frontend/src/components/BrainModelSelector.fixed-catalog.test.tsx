/**
 * A closed catalog must not be rendered as a choice.
 *
 * Field report 2026-07-29: the local Whisper card showed a MODEL dropdown with
 * a search box and a "type a custom id" row — for a provider that offers
 * exactly one model, the one the installer downloads. Two defects in one
 * control:
 *
 *  - a picker with a single entry asks the user to make a decision that does
 *    not exist;
 *  - the custom-id escape hatch let them name a model that is not on the
 *    machine, which for an on-device provider fails at the next spoken word.
 *
 * With `fixedCatalog` the control states what runs, and the custom row is gone
 * even when several voices ARE installed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BrainModelSelector } from "@/components/BrainModelSelector";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "apikeys_model.heading": "MODEL",
      "apikeys_model.heading_voice": "VOICE",
      "apikeys_model.choose_placeholder": "Choose a model…",
      "apikeys_model.choose_voice": "Choose a voice…",
      "apikeys_model.refresh": "Refresh",
      "apikeys_model.no_models": "No models found",
      "apikeys_model.loading": "Loading…",
      "apikeys_model.search_placeholder": "Search models or type a custom id…",
      "apikeys_model.use_custom": "Use {0}",
      "apikeys_model.model_label": "Model",
    })[key] ?? key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: unknown) => unknown) =>
    selector({ pushToast: () => {} }),
}));

function loader(ids: string[], selects: "model" | "voice" = "model") {
  return async () => ({
    provider: "faster-whisper",
    current_model: "",
    models: ids.map((id) => ({ id, label: id })),
    source: "static" as const,
    fetched_at: 0,
    selects,
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("fixed catalog", () => {
  it("states the single model instead of offering a dropdown", async () => {
    render(
      <BrainModelSelector
        providerId="faster-whisper"
        fixedCatalog
        loadModels={loader(["large-v3"])}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("fixed-model").textContent).toBe("large-v3");
    });
    expect(screen.queryByRole("button", { name: /model/i })).toBeNull();
    expect(screen.queryByPlaceholderText(/custom id/i)).toBeNull();
  });

  it("still offers a picker when several voices are installed", async () => {
    render(
      <BrainModelSelector
        providerId="piper-local"
        fixedCatalog
        loadModels={loader(["de-thorsten", "en-ryan", "es-dave"], "voice")}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByTestId("fixed-model")).toBeNull();
    });
    expect(screen.getByRole("button", { name: /model/i })).toBeTruthy();
  });

  it("never offers a custom id on a closed catalog", async () => {
    render(
      <BrainModelSelector
        providerId="piper-local"
        fixedCatalog
        loadModels={loader(["de-thorsten", "en-ryan"], "voice")}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /model/i })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /model/i }));
    const search = screen.getByPlaceholderText(/custom id/i);
    fireEvent.change(search, { target: { value: "some-model-nobody-downloaded" } });

    await waitFor(() => {
      expect(screen.queryByTestId("use-custom-row")).toBeNull();
    });
  });

  it("keeps the custom id for normal (cloud) providers", async () => {
    render(
      <BrainModelSelector
        providerId="openrouter"
        loadModels={loader(["a-model", "b-model"])}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /model/i })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /model/i }));
    fireEvent.change(screen.getByPlaceholderText(/custom id/i), {
      target: { value: "org/some-new-model" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("use-custom-row")).toBeTruthy();
    });
  });
});
