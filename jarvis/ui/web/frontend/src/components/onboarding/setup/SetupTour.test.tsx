import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { _resetProvidersCacheForTests } from "@/hooks/useProviders";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { loadLocaleChunk } from "@/i18n";
import { requestedApiKeysTab } from "@/lib/apiKeysTab";
import { useEventStore } from "@/store/events";
import { SetupTour } from "./SetupTour";

type Onb = ReturnType<typeof useOnboarding>;

const openai = {
  id: "openai",
  label: "OpenAI",
  tier: "brain",
  auth_mode: "api_key",
  secret_keys: ["openai_api_key"],
  secrets_set: {} as Record<string, boolean>,
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: false,
  active: false,
};

const plan = {
  id: "openai-live",
  label: "OpenAI GPT-Live",
  summary: "",
  mode: "realtime",
  recommended: true,
  assignments: { brain: "openai" },
  key_slots: [{ family: "openai", slot: "openai_api_key", label: "OpenAI", present: false }],
  keys_complete: false,
  ready_sections: [],
};

let providers = [openai];
let agentRows: Array<{ jarvis: string; label?: string; oauth_connected?: boolean }> = [];
let wakeWord = { phrase: "", enabled: false };
let calls: Array<{ url: string; method: string }> = [];

function stubFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      const reply = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      if (url === "/api/providers") return reply({ providers });
      if (url === "/api/setup/starter-plans") return reply({ plans: [plan], selected: null, custom_id: "custom" });
      if (url === "/api/settings/wake-word") return reply(wakeWord);
      if (url === "/api/jarvis-agent/status") return reply({ mapping: agentRows });
      if (url === "/api/settings/autostart") return reply({ enabled: false, supported: true });
      return reply({ ok: true });
    }),
  );
}

function fakeOnb(over: Partial<NonNullable<Onb["state"]>> = {}): Onb {
  return {
    state: {
      completed: false,
      current_step: null,
      skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false,
      tour_completed: false,
      legal_references: [],
      steps: [],
      ...over,
    },
    loading: false,
    error: null,
    refetch: vi.fn(async () => undefined),
    saveStep: vi.fn(async () => undefined),
    acceptTerms: vi.fn(async () => undefined),
    acknowledgeWakeWord: vi.fn(async () => undefined),
    complete: vi.fn(async () => undefined),
    completeTour: vi.fn(async () => undefined),
  };
}

const accepted = { terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" } };

beforeAll(async () => {
  await loadLocaleChunk("onboarding");
});

beforeEach(() => {
  providers = [{ ...openai, secrets_set: {} }];
  agentRows = [];
  wakeWord = { phrase: "", enabled: false };
  _resetProvidersCacheForTests();
  useEventStore.getState().setActiveSection("chats");
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("asks for the consent first and opens the API Keys page after it", async () => {
  const onb = fakeOnb();
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  const start = (await screen.findByTestId("onboarding-primary")) as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  fireEvent.click(screen.getByTestId("onboarding-accept"));
  await act(async () => {
    fireEvent.click(start);
  });
  expect(onb.acceptTerms).toHaveBeenCalled();
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("keys"));
  expect(onb.saveStep).toHaveBeenCalledWith("keys", []);
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("apikeys"));
});

it("waits for a key, and lets the user go on later", async () => {
  const onb = fakeOnb({ ...accepted, current_step: "keys" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-keys-waiting");
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByTestId("setup-keys-later"));
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("subscriptions"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("subscriptions", ["keys"]);
});

it("opens the Agents tab and waits for a subscription", async () => {
  const onb = fakeOnb({ ...accepted, current_step: "subscriptions" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  const status = await screen.findByTestId("setup-subscriptions-status");
  expect(useEventStore.getState().activeSection).toBe("apikeys");
  expect(requestedApiKeysTab()).toBe("subagents");
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(true);
  expect(status.textContent).not.toContain("Claude");
  fireEvent.click(screen.getByTestId("setup-subscriptions-later"));
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("voice"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("voice", ["subscriptions"]);
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("settings"));
  expect(requestedApiKeysTab()).toBeNull();
});

it("lets the user on once a subscription is signed in", async () => {
  agentRows = [{ jarvis: "claude-api", label: "Claude (API-Key)", oauth_connected: true }];
  render(<SetupTour onb={fakeOnb({ ...accepted, current_step: "subscriptions" })} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect(screen.getByTestId("setup-subscriptions-status").textContent).toContain("Claude"));
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false);
});

it("asks for a wake word before going on, with a way to leave it for later", async () => {
  const onb = fakeOnb({ ...accepted, current_step: "voice" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-voice-later");
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByTestId("setup-voice-later"));
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("ready"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("ready", ["voice"]);
});

it("goes on from the wake word once one is saved", async () => {
  wakeWord = { phrase: "Hey George", enabled: true };
  render(<SetupTour onb={fakeOnb({ ...accepted, current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByTestId("setup-voice-later")).toBeNull();
});

it("switches on the plan a key saved during the step completes", async () => {
  render(<SetupTour onb={fakeOnb({ ...accepted, current_step: "keys" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-keys-waiting");
  providers = [{ ...openai, secrets_set: { openai_api_key: true } }];
  act(() => {
    window.dispatchEvent(new CustomEvent("jarvis:secret-configured", { detail: { key: "openai_api_key", action: "set" } }));
  });
  await screen.findByTestId("setup-keys-connected");
  expect(calls.some((c) => c.url === "/api/brain/switch" && c.method === "POST")).toBe(true);
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false);
});

it("leaves a key that was already there exactly as it is", async () => {
  providers = [{ ...openai, secrets_set: { openai_api_key: true } }];
  render(<SetupTour onb={fakeOnb({ ...accepted, current_step: "keys" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-keys-present");
  expect(calls.some((c) => c.url.includes("/switch"))).toBe(false);
});

it("completes onboarding from the last step", async () => {
  const onb = fakeOnb({ ...accepted, current_step: "ready" });
  const onFinished = vi.fn();
  render(<SetupTour onb={onb} preview={false} onFinished={onFinished} />);
  const start = await screen.findByTestId("onboarding-start");
  await act(async () => {
    fireEvent.click(start);
  });
  expect(onb.complete).toHaveBeenCalled();
  expect(onFinished).not.toHaveBeenCalled();
});

it("walks a replay from the start and never writes, completes or restarts", async () => {
  const onb = fakeOnb({ ...accepted, completed: true, current_step: "voice" });
  const onFinished = vi.fn();
  render(<SetupTour onb={onb} preview onFinished={onFinished} />);
  // A replay shows every step, the consent included (already ticked).
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("welcome"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("keys"));
  fireEvent.click(await screen.findByTestId("setup-keys-later"));
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("subscriptions"));
  fireEvent.click(await screen.findByTestId("setup-subscriptions-later"));
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("voice"));
  fireEvent.click(await screen.findByTestId("setup-voice-later"));
  const start = await screen.findByTestId("onboarding-start");
  await act(async () => {
    fireEvent.click(start);
  });
  expect(onFinished).toHaveBeenCalled();
  expect(onb.acceptTerms).not.toHaveBeenCalled();
  expect(onb.saveStep).not.toHaveBeenCalled();
  expect(onb.complete).not.toHaveBeenCalled();
});

it("dims the app and keeps its card above every dialog", async () => {
  render(<SetupTour onb={fakeOnb()} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-card");
  const layer = screen.getByTestId("tour-layer");
  expect(layer.hasAttribute("data-tour-layer")).toBe(true);
  expect(screen.getByTestId("tour-dim").className).toContain("pointer-events-auto");
});

it("starts a replay from Settings at the API Keys page, with no way back to the consent", async () => {
  const onb = fakeOnb({ ...accepted, completed: true });
  render(<SetupTour onb={onb} preview startAt="keys" onFinished={vi.fn()} />);
  await waitFor(() => expect(screen.getByTestId("setup-card").dataset.step).toBe("keys"));
  expect(screen.queryByTestId("setup-back")).toBeNull();
});
