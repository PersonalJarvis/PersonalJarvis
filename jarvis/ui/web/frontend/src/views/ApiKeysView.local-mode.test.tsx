/**
 * Component tests for the Local Mode switch on the API-Keys screen.
 *
 * The switch answers a specific complaint: an install that went down the local
 * path landed in a console full of hosted cards, with no obvious, reversible
 * control over that. So the tests pin the reversibility as hard as the
 * filtering — the header control has to be present, readable, and able to put
 * every card back with one click.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    tier: "brain",
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

describe("ApiKeysView local mode", () => {
  it("sits in the header next to the engine switch and starts off", () => {
    render(<ApiKeysView />);

    const control = screen.getByTestId("local-mode-switch");
    expect(control.closest("header")).not.toBeNull();
    expect(control.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("voice-engine-header-control")).toBeTruthy();
    expect(screen.queryByTestId("local-mode-notice")).toBeNull();
  });

  it("hides hosted provider cards when switched on, and restores them when off", () => {
    mockProviders = [
      card("openai", "OpenAI", "api"),
      card("ollama", "Ollama", "local"),
    ];
    render(<ApiKeysView />);

    expect(screen.getByText("OpenAI")).toBeTruthy();

    fireEvent.click(screen.getByTestId("local-mode-switch"));

    expect(screen.queryByText("OpenAI")).toBeNull();
    expect(screen.getByText("Ollama")).toBeTruthy();
    expect(screen.getByTestId("local-mode-switch").getAttribute("aria-pressed")).toBe(
      "true",
    );

    fireEvent.click(screen.getByTestId("local-mode-switch"));

    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.queryByTestId("local-mode-notice")).toBeNull();
  });

  it("says how many cards it hid and offers a way back to all of them", () => {
    mockProviders = [
      card("openai", "OpenAI", "api"),
      card("gemini", "Gemini", "api"),
      card("ollama", "Ollama", "local"),
    ];
    render(<ApiKeysView />);

    fireEvent.click(screen.getByTestId("local-mode-switch"));

    const notice = screen.getByTestId("local-mode-notice");
    expect(notice.textContent).toContain("2");

    fireEvent.click(screen.getByText("Show all providers"));

    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.queryByTestId("local-mode-notice")).toBeNull();
  });

  it("keeps the running hosted provider visible so the tier is never blank", () => {
    mockProviders = [
      card("openai", "OpenAI", "api", true),
      card("ollama", "Ollama", "local"),
    ];
    render(<ApiKeysView />);

    fireEvent.click(screen.getByTestId("local-mode-switch"));

    expect(screen.getByText("OpenAI")).toBeTruthy();
    // Nothing was hidden, so there is nothing to explain.
    expect(screen.queryByTestId("local-mode-notice")).toBeNull();
  });

  it("survives a remount, because it is a stored preference", () => {
    mockProviders = [
      card("openai", "OpenAI", "api"),
      card("ollama", "Ollama", "local"),
    ];
    const first = render(<ApiKeysView />);
    fireEvent.click(screen.getByTestId("local-mode-switch"));
    first.unmount();

    render(<ApiKeysView />);
    expect(screen.getByTestId("local-mode-switch").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(screen.queryByText("OpenAI")).toBeNull();
  });
});
