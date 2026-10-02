import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProviderDescriptor } from "@/hooks/useProviders";
import { VoiceProviderSettings } from "./VoiceProviderSettings";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/views/apikeys/RealtimeTab", () => ({
  RealtimeTab: ({ providers, onActivateOptimistic }: {
    providers: ProviderDescriptor[];
    onActivateOptimistic: (tier: string, id: string) => void;
  }) => <div data-testid="shared-realtime-setup">
    {providers.map((provider) => <button key={provider.id}
      onClick={() => onActivateOptimistic("realtime", provider.id)}>{provider.label}</button>)}
  </div>,
}));

afterEach(cleanup);

const provider = (id: string, billing: ProviderDescriptor["billing"], tier = "realtime") => ({
  id, label: id, billing, tier,
} as ProviderDescriptor);

function setup(localMode: boolean, providers: ProviderDescriptor[]) {
  const onDisableLocalMode = vi.fn();
  const onActivateOptimistic = vi.fn();
  render(<VoiceProviderSettings providers={providers} loading={false} error={null}
    onChanged={vi.fn()} onActivateOptimistic={onActivateOptimistic}
    localMode={localMode} onDisableLocalMode={onDisableLocalMode} />);
  return { onDisableLocalMode, onActivateOptimistic };
}

it("preserves local-only filtering and the public tabpanel around shared setup", () => {
  setup(true, [provider("local-voice", "local"), provider("openai-live", "api"),
    provider("subscription", "subscription"), provider("local-brain", "local", "brain")]);
  expect(screen.getByRole("tabpanel", { name: "live.setup_title" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "local-voice" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "openai-live" })).toBeNull();
  expect(screen.queryByRole("button", { name: "subscription" })).toBeNull();
  expect(screen.queryByRole("button", { name: "local-brain" })).toBeNull();
  expect(screen.getByText("live.agents_separate")).toBeTruthy();
});

it("retains the cloud opt-in when local mode has no compatible voice", () => {
  const { onDisableLocalMode } = setup(true, [provider("openai-live", "api")]);
  fireEvent.click(screen.getByRole("button", { name: "live.show_cloud" }));
  expect(onDisableLocalMode).toHaveBeenCalledOnce();
});

it("forwards activation without removing existing API and other cloud providers", () => {
  const { onActivateOptimistic } = setup(false, [provider("openai-live", "api"),
    provider("gemini-live", "api"), provider("subscription", "subscription")]);
  fireEvent.click(screen.getByRole("button", { name: "gemini-live" }));
  expect(onActivateOptimistic).toHaveBeenCalledWith("realtime", "gemini-live");
  expect(screen.getByRole("button", { name: "openai-live" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "subscription" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "live.show_cloud" })).toBeNull();
});
