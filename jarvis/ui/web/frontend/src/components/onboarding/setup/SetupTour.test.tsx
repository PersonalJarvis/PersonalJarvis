import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { _resetProvidersCacheForTests } from "@/hooks/useProviders";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { loadLocaleChunk } from "@/i18n";
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

let providers = [openai];
let wakeWord = { phrase: "", enabled: false, engine: "auto" };
let cliStatus: Record<string, object> = {};
let loginReplies: Record<string, { status: number; body: unknown }> = {};
let calls: Array<{ url: string; method: string; body?: string }> = [];

function stubFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
      const reply = (body: unknown, status = 200) =>
        Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
      if (url === "/api/permissions/status") return reply({ platform: "win32" });
      if (url === "/api/providers") return reply({ providers });
      if (url === "/api/setup/starter-plans") return reply({ plans: [], selected: null, custom_id: "custom" });
      if (url === "/api/settings/wake-word" && method === "PUT") {
        const sent = JSON.parse(String(init?.body)) as { phrase: string };
        wakeWord = { ...wakeWord, phrase: sent.phrase };
        return reply({ ok: true, phrase: sent.phrase, engine: "auto", resolved_engine: "auto", degraded: false });
      }
      if (url === "/api/settings/wake-word") return reply(wakeWord);
      if (url === "/api/settings/keybinds") return reply({ keybinds: { call: "ctrl+alt+j" }, defaults: {}, suggestions: [] });
      if (url === "/api/settings/autostart" && method === "PUT") return reply({ ok: true, enabled: true, supported: true });
      if (url === "/api/settings/autostart") return reply({ enabled: false, supported: true });
      if (url.endsWith("/status") && url in cliStatus) return reply(cliStatus[url]);
      if (url.endsWith("/status")) return reply({ installed: true, connected: false, mode: "unknown" });
      if (url.endsWith("/login") && url in loginReplies) return reply(loginReplies[url].body, loginReplies[url].status);
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

const step = () => screen.getByTestId("setup-card").dataset.step;

beforeAll(async () => {
  await loadLocaleChunk("onboarding");
});

beforeEach(() => {
  providers = [{ ...openai, secrets_set: {} }];
  wakeWord = { phrase: "", enabled: false, engine: "auto" };
  cliStatus = {};
  loginReplies = {};
  _resetProvidersCacheForTests();
  window.sessionStorage.clear();
  useEventStore.getState().setActiveSection("chats");
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens the setup window on the name, with three step pills", async () => {
  render(<SetupTour onb={fakeOnb()} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-name-input");
  expect(step()).toBe("name");
  const pills = within(screen.getByTestId("setup-steps")).getAllByRole("button");
  expect(pills).toHaveLength(3);
  expect(screen.getByTestId("setup-pill-name").getAttribute("aria-current")).toBe("step");
});

it("asks for a name, saves it as the wake word and moves on to connecting", async () => {
  const onb = fakeOnb();
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  const input = (await screen.findByTestId("setup-name-input")) as HTMLInputElement;
  const go = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  fireEvent.change(input, { target: { value: "Nova" } });
  await act(async () => {
    fireEvent.click(go);
  });
  await waitFor(() => expect(step()).toBe("connect"));
  const put = calls.find((c) => c.url === "/api/settings/wake-word" && c.method === "PUT");
  expect(JSON.parse(put!.body!)).toMatchObject({ phrase: "Nova", persist: true });
  expect(onb.saveStep).toHaveBeenCalledWith("connect", []);
});

it("keeps a name that did not change without saving it again", async () => {
  wakeWord = { phrase: "Atlas", enabled: true, engine: "auto" };
  render(<SetupTour onb={fakeOnb()} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect((screen.getByTestId("setup-name-input") as HTMLInputElement).value).toBe("Atlas"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("connect"));
  expect(calls.some((c) => c.url === "/api/settings/wake-word" && c.method === "PUT")).toBe(false);
});

it("lists one row per provider, marks a signed-in one as ready and records a skipped step", async () => {
  cliStatus["/api/claude/status"] = { installed: true, connected: true, mode: "subscription", user_email: "a@b.c" };
  const onb = fakeOnb({ current_step: "connect" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  for (const id of ["claude", "openai", "google", "grok"]) await screen.findByTestId(`setup-sub-${id}`);
  await waitFor(() => expect(screen.getByTestId("setup-sub-claude").dataset.state).toBe("ready"));
  expect(screen.getByTestId("setup-sub-claude").textContent).toContain("a@b.c");
  // Something is connected: Continue moves on without a skip.
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("voice"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("voice", []);
});

it("lets the user go on with nothing connected, recorded as skipped", async () => {
  const onb = fakeOnb({ current_step: "connect" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-connect-nothing");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("voice"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("voice", ["connect"]);
});

it("shows the install command when a subscription's app is missing", async () => {
  cliStatus["/api/codex/status"] = { installed: false, connected: false, mode: "missing" };
  loginReplies["/api/codex/login"] = {
    status: 409,
    body: { detail: { message: "Codex CLI is not installed", install_command: "npm i -g @openai/codex" } },
  };
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  const connect = await screen.findByTestId("setup-sub-openai-connect");
  await act(async () => {
    fireEvent.click(connect);
  });
  const install = await screen.findByTestId("setup-sub-openai-install");
  expect(install.textContent).toContain("npm i -g @openai/codex");
  expect(calls.some((c) => c.url === "/api/codex/login" && c.method === "POST")).toBe(true);
});

it("takes an API key right in the provider's row", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  fireEvent.click(await screen.findByTestId("setup-sub-openai-key"));
  await screen.findByTestId("setup-key-panel-openai");
  // A row whose family has no key card offers only the sign-in.
  expect(screen.queryByTestId("setup-sub-claude-key")).toBeNull();
});

it("ends the window on the voice step and walks the app, then finishes", async () => {
  wakeWord = { phrase: "Nova", enabled: false, engine: "auto" };
  const onb = fakeOnb({ current_step: "voice" });
  const onFinished = vi.fn();
  render(<SetupTour onb={onb} preview={false} onFinished={onFinished} />);
  await waitFor(() => expect(screen.getByTestId("setup-voice-wake").textContent).toContain("Hey Nova"));
  await screen.findByTestId("onboarding-autostart");
  expect(screen.getByTestId("setup-voice-keys").textContent).toBe("CtrlAltJ");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-start"));
  });
  await waitFor(() => expect(step()).toBe("tour"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("tour", []);
  // The walk: chat first, then on through every stop to the end.
  expect(screen.getByTestId("setup-card").dataset.stop).toBe("chat");
  const seen: string[] = [];
  for (let i = 0; i < 12 && !onFinished.mock.calls.length; i++) {
    seen.push(screen.getByTestId("setup-card").dataset.stop ?? "");
    await act(async () => {
      fireEvent.click(screen.getByTestId("walk-next"));
    });
  }
  expect(seen).toEqual(["chat", "voice", "agents", "ide", "plugins", "wake", "done"]);
  expect(onFinished).toHaveBeenCalledTimes(1);
  expect(useEventStore.getState().activeSection).toBe("chats");
});

it("lets the walk be skipped", async () => {
  const onFinished = vi.fn();
  render(<SetupTour onb={fakeOnb({ current_step: "tour" })} preview={false} onFinished={onFinished} />);
  fireEvent.click(await screen.findByTestId("walk-skip"));
  expect(onFinished).toHaveBeenCalledTimes(1);
});

it("goes back to a finished step from its pill", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("onboarding-start");
  fireEvent.click(screen.getByTestId("setup-pill-name"));
  await waitFor(() => expect(step()).toBe("name"));
});

it("never writes the onboarding state in a replay", async () => {
  wakeWord = { phrase: "Nova", enabled: true, engine: "auto" };
  const onb = fakeOnb({ completed: true, current_step: "tour" });
  render(<SetupTour onb={onb} preview onFinished={vi.fn()} />);
  // A replay starts at the beginning, not at the saved step.
  await waitFor(() => expect((screen.getByTestId("setup-name-input") as HTMLInputElement).value).toBe("Nova"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("connect"));
  expect(onb.saveStep).not.toHaveBeenCalled();
});
