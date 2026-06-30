import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const {
  switchBrainProvider,
  switchTtsProvider,
  switchSttProvider,
  refetch,
  setActiveOptimistic,
  PROVIDERS,
} = vi.hoisted(() => {
    const mk = (over: Record<string, unknown>) => ({
      id: "x",
      label: "X",
      tier: "brain",
      auth_mode: "api_key",
      secret_keys: ["x_api_key"],
      secrets_set: {},
      dashboard_url: null,
      login_cli: null,
      install_hint: null,
      credential_path_hint: null,
      configured: false,
      active: false,
      cli_installed: null,
      ...over,
    });
    return {
      switchBrainProvider: vi.fn().mockResolvedValue(undefined),
      switchTtsProvider: vi.fn().mockResolvedValue({}),
      switchSttProvider: vi.fn().mockResolvedValue({}),
      refetch: vi.fn(),
      setActiveOptimistic: vi.fn(),
      PROVIDERS: [
        mk({ id: "claude-api", label: "Claude", tier: "brain", secret_keys: ["anthropic_api_key"], configured: true }),
        mk({ id: "gemini", label: "Gemini", tier: "brain", secret_keys: ["gemini_api_key"], configured: false }),
        mk({ id: "cartesia", label: "Cartesia", tier: "tts", secret_keys: ["cartesia_api_key"], configured: false }),
        mk({ id: "deepgram", label: "Deepgram", tier: "stt", secret_keys: ["deepgram_api_key"], configured: false }),
      ],
    };
  });

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/ApiKeyForm", () => ({
  ApiKeyForm: ({ secretKey }: { secretKey: string }) => (
    <div data-testid="keyform">{secretKey}</div>
  ),
}));
// The subagent section fetches /api/jarvis-agent/status on mount, which jsdom has no
// server for. Stub it so the step renders deterministically; its real behaviour
// is covered by JarvisAgentSection's own tests.
vi.mock("@/components/JarvisAgentSection", () => ({
  JarvisAgentSection: () => <div data-testid="subagent-section" />,
}));
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({
    providers: PROVIDERS,
    loading: false,
    error: null,
    refetch,
    setActiveOptimistic,
  }),
  switchBrainProvider,
  switchTtsProvider,
  switchSttProvider,
}));

import { ApiKeysStep } from "./ApiKeysStep";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderStep(over: Record<string, unknown> = {}) {
  const props = {
    onb: {} as never,
    goNext: vi.fn(),
    goBack: vi.fn(),
    skip: vi.fn(),
    isFirst: false,
    isLast: false,
    ...over,
  };
  render(<ApiKeysStep {...props} />);
  return props;
}

it("shows every provider class and the subagent section at once", () => {
  renderStep();

  // All three tier headers are visible simultaneously — no paging.
  expect(screen.getByText("Brain")).toBeTruthy();
  expect(screen.getByText("Voice")).toBeTruthy();
  expect(screen.getByText("Hearing")).toBeTruthy();

  // ...and their providers, in order, in the same scroll container.
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(screen.getByText("anthropic_api_key")).toBeTruthy();
  expect(screen.getByText("Cartesia")).toBeTruthy();
  expect(screen.getByText("Deepgram")).toBeTruthy();

  // The subagent class is folded in below the key tiers.
  expect(screen.getByTestId("subagent-section")).toBeTruthy();
});

it("advances the flow directly on Next (no internal paging)", () => {
  const props = renderStep();
  fireEvent.click(screen.getByRole("button", { name: "onboarding.nav.next" }));
  expect(props.goNext).toHaveBeenCalledTimes(1);
});

it("skips the whole step", () => {
  const props = renderStep();
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.api_keys.skip" }),
  );
  expect(props.skip).toHaveBeenCalled();
});

it("activates a configured provider via its select control", async () => {
  renderStep();
  // Claude is configured → selectable. Its select button's accessible name is "Claude".
  fireEvent.click(screen.getByRole("button", { name: /Claude/ }));
  expect(switchBrainProvider).toHaveBeenCalledWith("claude-api");
  await waitFor(() => expect(refetch).toHaveBeenCalled());
});

it("flips the highlight optimistically before the switch resolves", () => {
  // A switch that never resolves proves the UI does not wait on the backend.
  switchBrainProvider.mockReturnValueOnce(new Promise(() => {}));
  renderStep();
  fireEvent.click(screen.getByRole("button", { name: /Claude/ }));
  // The optimistic active-flip fires synchronously on click, ahead of the
  // (still-pending) switch call and any refetch.
  expect(setActiveOptimistic).toHaveBeenCalledWith("brain", "claude-api");
});

it("disables selection for an unconfigured cloud provider", () => {
  renderStep();
  const gemini = screen.getByRole("button", { name: /Gemini/ }) as HTMLButtonElement;
  expect(gemini.disabled).toBe(true);
  expect(switchBrainProvider).not.toHaveBeenCalled();
});
