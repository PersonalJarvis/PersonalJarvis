/** Legacy local-mode preferences must not hide realtime provider choices. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ProviderDescriptor } from "@/hooks/useProviders";

function card(
  id: string,
  label: string,
  billing: ProviderDescriptor["billing"],
  active = false,
): ProviderDescriptor {
  return {
    id,
    label,
    tier: "realtime",
    auth_mode: billing === "local" ? "none" : "api_key",
    secret_keys: billing === "local" ? [] : [`${id}_api_key`],
    secrets_set: {},
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: false,
    active,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing,
    alt_credential: null,
  };
}

let mockProviders: ProviderDescriptor[] = [];

vi.mock("@/hooks/useProviders", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useProviders")>();
  return {
    ...actual,
    useProviders: () => ({
      providers: mockProviders,
      loading: false,
      error: null,
      refetch: vi.fn(),
      setActiveOptimistic: vi.fn(),
    }),
    useSectionHealth: () => ({ health: {}, reload: vi.fn() }),
  };
});

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
    statusKnown: true,
    connecting: false,
    requiresWebRtcOffer: false,
    transportOfferReady: null,
    transportOfferDetail: "",
    transportIssue: null,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
    transitioning: false,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

import { setLocalMode } from "@/lib/localMode";
import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  setLocalMode(false);
  window.localStorage.clear();
  mockProviders = [];
});

describe("ApiKeysView provider visibility", () => {
  it.each([false, true])("shows local and hosted choices with legacy local-mode=%s", (localMode) => {
    setLocalMode(localMode);
    mockProviders = [card("gemini-live", "Gemini Live", "api"), card("local-voice", "Local Voice", "local")];
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><ApiKeysView /></QueryClientProvider>);
    expect(screen.getByTestId("realtime-provider-gemini-live")).toBeTruthy();
    expect(screen.getByTestId("realtime-provider-local-voice")).toBeTruthy();
    expect(screen.queryByTestId("local-mode-switch")).toBeNull();
    view.unmount();
    client.clear();
  });
});
