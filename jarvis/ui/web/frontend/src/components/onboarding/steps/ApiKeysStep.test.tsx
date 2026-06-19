import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const { switchBrainProvider, switchTtsProvider, switchSttProvider, refetch, PROVIDERS } =
  vi.hoisted(() => {
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
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({ providers: PROVIDERS, loading: false, error: null, refetch }),
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

it("pages Brain -> Voice -> Hearing, then advances the flow", () => {
  const props = renderStep();
  const next = () =>
    fireEvent.click(screen.getByRole("button", { name: "onboarding.nav.next" }));

  expect(screen.getByText("Brain — reasoning")).toBeTruthy();
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(screen.getByText("anthropic_api_key")).toBeTruthy();

  next(); // -> Voice (TTS)
  expect(screen.getByText("Voice — text to speech")).toBeTruthy();
  expect(screen.getByText("Cartesia")).toBeTruthy();

  next(); // -> Hearing (STT)
  expect(screen.getByText("Hearing — speech to text")).toBeTruthy();
  expect(props.goNext).not.toHaveBeenCalled();

  next(); // last class -> advance the flow
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

it("disables selection for an unconfigured cloud provider", () => {
  renderStep();
  const gemini = screen.getByRole("button", { name: /Gemini/ }) as HTMLButtonElement;
  expect(gemini.disabled).toBe(true);
  expect(switchBrainProvider).not.toHaveBeenCalled();
});
