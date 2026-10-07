/**
 * The voice section's API Keys tab, drawn in the voice row grammar.
 *
 * The provider data, order and actions are the shared `TierSection` ones; what
 * these tests pin is what this screen promises on top: one section per tier,
 * the provider the tier runs on first and marked Active, the wording tier's
 * unpinned state said as "Automatic", and every other provider a compact row
 * that opens in place and can be made active from here.
 *
 * Only the hook's data source is replaced — the switch and model calls go
 * through the real functions to a stubbed `fetch`, so the request each action
 * sends is what is asserted.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { ProviderDescriptor } from "@/hooks/useProviders";

function card(over: Partial<ProviderDescriptor>): ProviderDescriptor {
  return {
    id: "speech-a",
    label: "Speech A",
    tier: "stt",
    auth_mode: "api_key",
    secret_keys: ["SPEECH_A_KEY"],
    secrets_set: { SPEECH_A_KEY: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: false,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    alt_credential: null,
    ...over,
  };
}

const state = vi.hoisted(() => ({
  providers: [] as ProviderDescriptor[],
  setActiveOptimistic: (() => {}) as (tier: string, id: string) => void,
}));

vi.mock("@/hooks/useProviders", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useProviders")>()),
  useProviders: () => ({
    providers: state.providers,
    loading: false,
    error: null,
    refetch: () => {},
    setActiveOptimistic: state.setActiveOptimistic,
  }),
  useSectionHealth: () => ({ health: {} }),
}));

import { VoiceApiKeysTab } from "@/views/voice/VoiceApiKeysTab";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  state.providers = [
    // Catalog order puts the active provider second; the list must lead with it.
    card({ id: "speech-a", label: "Speech A" }),
    card({ id: "speech-b", label: "Speech B", active: true }),
    card({
      id: "speech-c",
      label: "Speech C",
      configured: false,
      secret_keys: ["SPEECH_C_KEY"],
      secrets_set: { SPEECH_C_KEY: false },
    }),
    card({
      id: "wording-a",
      label: "Wording A",
      tier: "dictation",
      optional: true,
      secret_keys: ["WORDING_A_KEY"],
      secrets_set: { WORDING_A_KEY: false },
      configured: false,
    }),
  ];
  state.setActiveOptimistic = vi.fn();
  fetchMock = vi.fn(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({ restart_required: false, models: [] }),
        text: async () => "{}",
      }) as Response,
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function rowIds(tier: string): string[] {
  return within(screen.getByTestId(`voice-provider-tier-${tier}`))
    .getAllByTestId(/^provider-card-/)
    .map((el) => el.getAttribute("data-testid")!.replace("provider-card-", ""));
}

describe("VoiceApiKeysTab layout", () => {
  it("gives each tier its own section with a heading and one line", () => {
    render(<VoiceApiKeysTab hideHeader />);

    expect(screen.getByRole("heading", { name: "Speech recognition" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Text model" })).toBeTruthy();
    expect(rowIds("stt")).toEqual(["speech-b", "speech-a", "speech-c"]);
    expect(rowIds("dictation")).toEqual(["wording-a"]);
  });

  it("leads with the active provider and marks it Active", () => {
    render(<VoiceApiKeysTab hideHeader />);

    const active = screen.getByTestId("provider-row-speech-b");
    const radio = within(active).getByRole("radio") as HTMLInputElement;
    expect(radio.checked).toBe(true);
    expect(active.textContent).toContain("Active");
    // The others offer the switch instead.
    expect(screen.getByTestId("provider-row-speech-a").textContent).toContain("Set active");
    // The tier's own provider opens on arrival: its key, model and test.
    expect(screen.getByTestId("provider-body-speech-b")).toBeTruthy();
    expect(screen.queryByTestId("provider-body-speech-a")).toBeNull();
  });

  it("says Automatic when the wording tier has no pinned provider", () => {
    render(<VoiceApiKeysTab hideHeader />);

    expect(screen.getByTestId("voice-provider-tier-dictation-auto").textContent).toBe(
      "Automatic",
    );
    expect(screen.queryByTestId("voice-provider-tier-stt-auto")).toBeNull();
  });

  it("makes a configured provider active from its row", async () => {
    render(<VoiceApiKeysTab hideHeader />);

    fireEvent.click(within(screen.getByTestId("provider-row-speech-a")).getByRole("radio"));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]) === "/api/stt/switch");
      expect(call).toBeTruthy();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({
        provider: "speech-a",
        persist: true,
      });
    });
    expect(state.setActiveOptimistic).toHaveBeenCalledWith("stt", "speech-a");
  });

  it("opens a provider in place for its key, one row at a time", () => {
    render(<VoiceApiKeysTab hideHeader />);

    // A provider without a key only opens; it does not switch.
    fireEvent.click(screen.getByTestId("provider-row-speech-c"));
    const body = screen.getByTestId("provider-body-speech-c");
    expect(within(body).getByLabelText("Enter SPEECH_C_KEY")).toBeTruthy();
    expect(screen.queryByTestId("provider-body-speech-b")).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/switch"))).toBe(false);
  });

  it("offers the model picker on the active provider without probing anything", async () => {
    render(<VoiceApiKeysTab hideHeader />);

    // The picker reads the provider's model list ...
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).startsWith("/api/providers/speech-b/models"),
        ),
      ).toBe(true),
    );
    // ... and nothing on this screen runs a provider test the user did not ask for.
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/test"))).toBe(false);
  });
});
