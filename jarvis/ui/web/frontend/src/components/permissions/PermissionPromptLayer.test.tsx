import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/i18n";
import { resetConnectBudgetForTests } from "@/lib/connectBudget";
import { EMPTY_PROMPTS, PERMISSION_CARD_OFFSET_VAR } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";
import { useSettingsJump } from "@/store/settingsJump";
import PermissionPromptLayer from "./PermissionPromptLayer";

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let rows: Record<string, Record<string, unknown>> = {};
let restartStatus = 200;

function row(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    label: id,
    status: "denied",
    used_for: [],
    can_request: false,
    can_open_settings: true,
    can_reset: true,
    restart_hint: false,
    detail: "",
    settings_path: "System Settings > Privacy & Security > Microphone",
    ...overrides,
  };
}

function ensure(overrides: Record<string, unknown> = {}) {
  return {
    permission: "microphone",
    outcome: "pending",
    granted: false,
    state: "not_determined",
    asked: true,
    outside_installed_app: false,
    reason: "not_determined",
    can_prompt: false,
    can_open_settings: true,
    target: "",
    user_detail: "",
    agent_detail: "",
    ...overrides,
  };
}

function installFetch(overrides: (call: Call) => unknown | undefined = () => undefined) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const call: Call = {
        url,
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      calls.push(call);
      const custom = overrides(call);
      if (custom !== undefined) {
        const status = (custom as { __status?: number }).__status ?? 200;
        return { ok: status < 400, status, json: async () => custom } as Response;
      }
      if (url === "/api/settings/restart-app") {
        return { ok: restartStatus < 400, status: restartStatus, json: async () => ({}) } as Response;
      }
      const match = /^\/api\/permissions\/([a-z_]+)(?:\/(request|open-settings|reset))?/.exec(url);
      if (match && call.method === "GET") {
        return { ok: true, status: 200, json: async () => rows[match[1]] ?? row(match[1]) } as Response;
      }
      if (match && match[2] === "request") {
        return { ok: true, status: 200, json: async () => ensure({ permission: match[1] }) } as Response;
      }
      if (match && match[2] === "open-settings") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ok: true, permission_id: match[1], action: "open_settings", performed: true, dry_run: false, message: "", permission: null }),
        } as Response;
      }
      if (match && match[2] === "reset") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            permission_id: match[1],
            action: "reset",
            performed: true,
            dry_run: false,
            message: "",
            permission: row(match[1], { status: "not_determined", can_request: true }),
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    }),
  );
}

function needed(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "dictation",
    reason: "denied",
    phase: "blocked",
    origin: "user",
    target: "",
    can_prompt: false,
    can_open_settings: true,
    outside_app: false,
    detail: "",
    ...overrides,
  };
}

function open(payload: Record<string, unknown>, trace = "t1", ts = Date.now()) {
  act(() => usePermissionsStore.getState().ingest("PermissionNeeded", trace, payload, ts));
}

const posts = (suffix: string) =>
  calls.filter((call) => call.method === "POST" && call.url.includes(suffix));

beforeEach(() => {
  calls = [];
  rows = {};
  restartStatus = 200;
  resetConnectBudgetForTests();
  useI18nStore.getState().setUi("en", { push: false });
  usePermissionsStore.setState({
    ...EMPTY_PROMPTS,
    snapshot: {
      platform: "darwin",
      supported: true,
      headless: false,
      app_identity: { app_name: "Personal Jarvis", bundle_id: "x", bundle_path: null, launched_as_bundle: true, stable: true },
      outside_installed_app: false,
      permissions: [],
      needed: [],
    },
    owner: true,
    inline: {},
    dictationNote: null,
  });
  installFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.documentElement.style.removeProperty(PERMISSION_CARD_OFFSET_VAR);
});

describe("what opens the card", () => {
  it("shows one card for an episode that needs the person, with the sentence from i18n", async () => {
    render(<PermissionPromptLayer />);
    open(needed());

    const card = await screen.findByTestId("permission-prompt-card");
    expect(card.getAttribute("role")).toBe("group");
    expect(screen.getByTestId("permission-prompt-sentence").textContent).toBe(
      "Dictation cannot work because access to “Microphone” is turned off for Personal Jarvis. Turn it on in System Settings, then come back.",
    );
    expect(within(card).getByRole("heading", { name: "Access is turned off" })).toBeTruthy();
  });

  it("never opens while macOS is asking by itself (phase os_dialog)", () => {
    render(<PermissionPromptLayer />);
    open(needed({ phase: "os_dialog", reason: "not_determined" }));

    expect(screen.queryByTestId("permission-prompt-card")).toBeNull();
  });

  it("never opens for a background consumer", () => {
    render(<PermissionPromptLayer />);
    open(needed({ origin: "background" }));

    expect(screen.queryByTestId("permission-prompt-card")).toBeNull();
  });

  it("draws nothing outside the owner window or on a headless backend", () => {
    usePermissionsStore.setState({ owner: false });
    const { rerender } = render(<PermissionPromptLayer />);
    open(needed());
    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();

    usePermissionsStore.setState({
      owner: true,
      snapshot: { ...usePermissionsStore.getState().snapshot!, headless: true },
    });
    rerender(<PermissionPromptLayer />);
    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
  });

  it("does not repeat a feature whose inline surface is on screen", () => {
    render(<PermissionPromptLayer />);
    act(() => {
      usePermissionsStore.getState().registerInline("dictation");
    });
    open(needed());

    expect(screen.queryByTestId("permission-prompt-card")).toBeNull();
  });

  it("shows ONE card plus '+N more', and the next one on request", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ feature: "voice" }), "a", 1_000);
    open(needed({ feature: "wake_word" }), "b", 2_000);
    open(needed({ feature: "appshot", permissions: ["screen_recording"] }), "c", 3_000);

    expect(await screen.findAllByTestId("permission-prompt-card")).toHaveLength(1);
    expect(screen.getByTestId("permission-prompt-card").getAttribute("data-feature")).toBe("appshot");
    const more = screen.getByTestId("permission-prompt-more");
    expect(more.textContent).toBe("+2 more");

    fireEvent.click(more);
    expect(screen.getByTestId("permission-prompt-card").getAttribute("data-feature")).toBe("wake_word");
  });
});

/** Switch the snapshot to "started from another app" (a terminal), not a .app copy outside Applications. */
function startedFromAnotherApp(): void {
  const { snapshot } = usePermissionsStore.getState();
  usePermissionsStore.setState({
    snapshot: snapshot && { ...snapshot, app_identity: { ...snapshot.app_identity, launched_as_bundle: false } },
  });
}

describe("the buttons per reason", () => {
  it("denied: Open System Settings opens the pane of the first missing permission", async () => {
    render(<PermissionPromptLayer />);
    open(needed());
    const button = await screen.findByRole("button", { name: "Open System Settings" });

    fireEvent.click(button);

    await waitFor(() => expect(posts("/api/permissions/microphone/open-settings")).toHaveLength(1));
    expect(screen.getByTestId("permission-prompt-path").textContent).toBe(
      "Find it under System Settings > Privacy & Security > Microphone.",
    );
  });

  it("not_determined: Continue asks, attributed to the feature", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ reason: "not_determined", can_prompt: true, can_open_settings: false, permissions: ["screen_recording"], feature: "screen_context" }));

    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));

    await waitFor(() => expect(posts("/api/permissions/screen_recording/request")).toHaveLength(1));
    expect(posts("/request")[0].body).toEqual({ feature: "screen_context" });
  });

  it("restricted and unavailable: an explanation and Not now, nothing else", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ reason: "restricted", can_open_settings: false, can_prompt: true }));

    const card = await screen.findByTestId("permission-prompt-card");
    // The quiet "See all permissions" link only navigates; it is not an action on the permission.
    const actions = within(card)
      .getAllByRole("button")
      .filter((b) => b.dataset.testid !== "permission-see-all");
    expect(actions.map((b) => b.textContent)).toEqual(["Not now"]);
  });

  it("restart_hint: Quit and reopen restarts the app through the shared guard", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ reason: "restart_hint", can_open_settings: false }));

    fireEvent.click(await screen.findByRole("button", { name: "Quit and reopen" }));

    await waitFor(() => expect(posts("/api/settings/restart-app")).toHaveLength(1));
  });

  it("restart_hint: a refused restart (missions running) arms the force label instead of killing missions", async () => {
    restartStatus = 409;
    render(<PermissionPromptLayer />);
    open(needed({ reason: "restart_hint", can_open_settings: false }));

    fireEvent.click(await screen.findByRole("button", { name: "Quit and reopen" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Quit and reopen" })).toBeNull());
    expect(posts("force=true")).toHaveLength(0);
  });

  it("outside the installed app the confirmation names the grantee and sends the consent flag", async () => {
    startedFromAnotherApp();
    render(<PermissionPromptLayer />);
    open(needed({ reason: "needs_settings", can_prompt: true, outside_app: true }));

    const confirm = await screen.findByRole("button", { name: "Allow for the app that started Personal Jarvis" });
    expect(screen.getByTestId("permission-prompt-sentence").textContent).toContain("started from another app");
    expect(posts("/request")).toHaveLength(0); // nothing is asked before the confirmation
    fireEvent.click(confirm);

    await waitFor(() => expect(posts("/request")).toHaveLength(1));
    expect(posts("/request")[0].body).toEqual({ feature: "dictation", allow_outside_app: true });
  });

  it.each(["not_determined", "needs_settings"] as const)(
    "outside the installed app (%s) the sentence matches the button instead of promising an OS dialog or a Settings switch",
    async (reason) => {
      startedFromAnotherApp();
      render(<PermissionPromptLayer />);
      open(needed({ reason, can_prompt: true, outside_app: true }));

      await screen.findByRole("button", { name: "Allow for the app that started Personal Jarvis" });
      const sentence = screen.getByTestId("permission-prompt-sentence").textContent ?? "";
      // The grantee is named (and what the grant covers), and the button is said to be the ask.
      expect(sentence).toMatch(/^Personal Jarvis was started from another app/);
      expect(sentence).toContain("access to “Microphone” for that app and for everything you run in it");
      expect(sentence).toContain("The button asks macOS now");
      // None of the per-feature promises that contradict that button.
      expect(sentence).not.toMatch(/Continue and macOS will ask you|Switch it on for/);
      // The heading agrees, and the old outside note is not repeated under the sentence.
      expect(screen.getByRole("group").getAttribute("aria-labelledby")).toBeTruthy();
      expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Confirm before macOS asks");
      const card = screen.getByTestId("permission-prompt-card").textContent ?? "";
      expect(card.match(/started from another app/g)).toHaveLength(1);
    },
  );

  it("keeps the feature sentence and the outside note when the reason is still a denial", async () => {
    startedFromAnotherApp();
    render(<PermissionPromptLayer />);
    open(needed({ reason: "denied", can_prompt: true, outside_app: true }));

    await screen.findByRole("button", { name: "Allow for the app that started Personal Jarvis" });
    expect(screen.getByTestId("permission-prompt-sentence").textContent).toContain("is turned off for Personal Jarvis");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Access is turned off");
    expect(screen.getByText(/started from another app/)).toBeTruthy();
  });

  it("a .app run from a disk image says so and offers to ask macOS, with no terminal in sight", async () => {
    render(<PermissionPromptLayer />); // the default snapshot is a .app copy (launched_as_bundle)
    open(needed({ reason: "needs_settings", can_prompt: true, outside_app: true }));

    const confirm = await screen.findByRole("button", { name: "Ask macOS now" });
    const sentence = screen.getByTestId("permission-prompt-sentence").textContent ?? "";
    expect(sentence).toContain("running from outside your Applications folder");
    expect(sentence).not.toMatch(/terminal/i);
    fireEvent.click(confirm);

    await waitFor(() => expect(posts("/request")).toHaveLength(1));
    expect(posts("/request")[0].body).toEqual({ feature: "dictation", allow_outside_app: true });
  });

  it("inside the installed app the feature sentence is unchanged", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ reason: "not_determined", can_prompt: true }));

    await screen.findByRole("button", { name: "Continue" });
    expect(screen.getByTestId("permission-prompt-sentence").textContent).toBe(
      "Dictation needs access to “Microphone”. Continue and macOS will ask you.",
    );
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("macOS will ask you next");
  });

  it("Not now hides the card and sends nothing", async () => {
    render(<PermissionPromptLayer />);
    open(needed());

    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));

    expect(screen.queryByTestId("permission-prompt-card")).toBeNull();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("a 429 says to wait instead of showing a status code", async () => {
    installFetch((call) =>
      call.url.includes("/open-settings")
        ? { __status: 429, error: "rate_limited", retry_after_s: 5 }
        : undefined,
    );
    render(<PermissionPromptLayer />);
    open(needed());

    fireEvent.click(await screen.findByRole("button", { name: "Open System Settings" }));

    expect((await screen.findByTestId("permission-prompt-message")).textContent).toBe(
      "Please wait a few seconds before trying again.",
    );
  });

  it("shows the backend detail only inside a collapsed section", async () => {
    render(<PermissionPromptLayer />);
    open(needed({ detail: "Microphone access is off for Personal Jarvis." }));

    const details = (await screen.findByText("Details")).closest("details")!;
    expect(details.hasAttribute("open")).toBe(false);
    expect(screen.getByTestId("permission-prompt-sentence").textContent).not.toContain("is off for Personal Jarvis.\n");
  });
});

describe("several permissions in one episode", () => {
  it("lists ordered steps with their state and opens the first one still missing", async () => {
    rows = {
      screen_recording: row("screen_recording", { status: "granted", can_reset: false }),
      accessibility: row("accessibility", { status: "not_granted" }),
    };
    render(<PermissionPromptLayer />);
    open(needed({ feature: "computer_use", permissions: ["screen_recording", "accessibility"], reason: "needs_settings" }));

    const steps = await screen.findByTestId("permission-prompt-steps");
    await waitFor(() => expect(within(steps).getByText("Granted")).toBeTruthy());
    // Only the missing permission is named in the sentence.
    expect(screen.getByTestId("permission-prompt-sentence").textContent).toContain("“Accessibility”");
    expect(screen.getByTestId("permission-prompt-sentence").textContent).not.toContain("Screen Recording");

    fireEvent.click(screen.getByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/api/permissions/accessibility/open-settings")).toHaveLength(1));
  });
});

describe("coming back from System Settings", () => {
  it("offers 'Already on? Reset and ask again' only when it still reads off, and verifies after the reset", async () => {
    rows = { microphone: row("microphone", { status: "denied", can_reset: true }) };
    render(<PermissionPromptLayer />);
    open(needed());
    fireEvent.click(await screen.findByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/open-settings")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Already on? Reset and ask again" })).toBeNull();

    // The person returns; the window regains focus and the row still says off.
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    const reset = await screen.findByRole("button", { name: "Already on? Reset and ask again" }, { timeout: 2_000 });

    fireEvent.click(reset);
    await waitFor(() => expect(posts("/microphone/reset")).toHaveLength(1));
    // After a reset that worked (the row reads not_determined) macOS is asked again.
    await waitFor(() => expect(posts("/microphone/request")).toHaveLength(1));
    expect((await screen.findByTestId("permission-prompt-message")).textContent).toBe(
      "Reset. macOS will ask again now.",
    );
  });

  it("suggests Quit and reopen before a reset for Screen Recording, which macOS may apply only to a new process", async () => {
    rows = { screen_recording: row("screen_recording", { status: "not_granted", can_reset: true }) };
    render(<PermissionPromptLayer />);
    open(needed({ feature: "computer_use", permissions: ["screen_recording"], reason: "needs_settings" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/open-settings")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Quit and reopen" })).toBeNull();

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    const restart = await screen.findByRole("button", { name: "Quit and reopen" }, { timeout: 2_000 });
    const reset = screen.getByRole("button", { name: "Already on? Reset and ask again" });
    expect(restart.compareDocumentPosition(reset) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("permission-prompt-restart-maybe").textContent).toBe(
      "macOS may apply this only after Personal Jarvis restarts.",
    );
  });

  it("falls back to the manual path (and the reboot hint) when the reset did not take", async () => {
    rows = { microphone: row("microphone", { status: "denied", can_reset: true }) };
    installFetch((call) =>
      call.url.includes("/microphone/reset")
        ? {
            ok: true,
            permission_id: "microphone",
            action: "reset",
            performed: true,
            dry_run: false,
            message: "",
            permission: row("microphone", { status: "denied" }),
          }
        : undefined,
    );
    render(<PermissionPromptLayer />);
    open(needed());
    fireEvent.click(await screen.findByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(posts("/open-settings")).toHaveLength(1));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    fireEvent.click(await screen.findByRole("button", { name: "Already on? Reset and ask again" }, { timeout: 2_000 }));

    expect((await screen.findByTestId("permission-prompt-message")).textContent).toContain("restart your Mac");
    expect(posts("/microphone/request")).toHaveLength(0);
  });

  it("offers a quiet way to Settings > Privacy, which only navigates", async () => {
    render(<PermissionPromptLayer />);
    open(needed());

    fireEvent.click(await screen.findByRole("button", { name: "See all permissions" }));

    expect(useSettingsJump.getState().target).toBe("permissions");
    expect(useEventStore.getState().activeSection).toBe("settings");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    useSettingsJump.getState().take();
  });

  it("Check again re-reads the row and says so when it is still off", async () => {
    rows = { microphone: row("microphone", { status: "denied" }) };
    render(<PermissionPromptLayer />);
    open(needed());
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/permissions/microphone")).toHaveLength(1));

    fireEvent.click(await screen.findByRole("button", { name: "Check again" }));

    expect((await screen.findByTestId("permission-prompt-message")).textContent).toContain("still looks off");
    expect(calls.filter((c) => c.url === "/api/permissions/microphone").length).toBeGreaterThanOrEqual(2);
  });
});

describe("no polling", () => {
  it("reads the row once when the card appears and never on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    render(<PermissionPromptLayer />);
    open(needed());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const reads = () => calls.filter((c) => c.method === "GET").length;
    expect(reads()).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });

    expect(reads()).toBe(1);
  });

  it("refetches once, single-flight and jittered, when the window regains focus", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    render(<PermissionPromptLayer />);
    open(needed());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const reads = () => calls.filter((c) => c.method === "GET").length;
    expect(reads()).toBe(1);

    act(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    expect(reads()).toBe(1); // not in the same tick
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(reads()).toBe(2);
  });

  it("sends ?activated=1 on the first refetch after the window regained focus, not on the card's own first read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    render(<PermissionPromptLayer />);
    open(needed());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    const reads = calls.filter((c) => c.method === "GET").map((c) => c.url);
    expect(reads).toEqual(["/api/permissions/microphone", "/api/permissions/microphone?activated=1"]);
  });

  it("keeps the activated hint when the focus arrives while a read is already running", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    let release: (() => void) | null = null;
    let first = true;
    installFetch((call) => {
      if (call.method === "GET" && first) {
        first = false;
        return new Promise<void>((resolve) => {
          release = resolve;
        }).then(() => row("microphone"));
      }
      return undefined;
    });
    render(<PermissionPromptLayer />);
    open(needed());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(1);

    await act(async () => {
      release?.();
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(calls.filter((c) => c.method === "GET").map((c) => c.url)).toEqual([
      "/api/permissions/microphone",
      "/api/permissions/microphone?activated=1",
    ]);
  });

  it("asks nothing on mount or on a return to the window (only a button press ever POSTs)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    render(<PermissionPromptLayer />);
    open(needed());
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});

describe("the confirmation", () => {
  it("turns a resolved card into 'Allowed' with a polite live region for at least five seconds", async () => {
    render(<PermissionPromptLayer />);
    open(needed());
    await screen.findByTestId("permission-prompt-card");

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "t1", { permissions: ["microphone"], feature: "dictation", granted: true }, Date.now());
    });

    expect(await screen.findByTestId("permission-allowed-card")).toBeTruthy();
    const live = screen.getByTestId("permission-live-region");
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("Allowed. You can carry on.");
    expect(screen.queryByTestId("permission-prompt-card")).toBeNull();
  });

  it("tells the person to ask again after a Computer Use mission stopped for the permission", async () => {
    // The mission already ended (blocked_permission); nothing resumes it after the grant.
    render(<PermissionPromptLayer />);
    open(needed({ feature: "computer_use", permissions: ["accessibility"] }));
    await screen.findByTestId("permission-prompt-card");

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "t1", { permissions: ["accessibility"], feature: "computer_use", granted: true }, Date.now());
    });

    expect(await screen.findByTestId("permission-allowed-card")).toBeTruthy();
    expect(screen.getByTestId("permission-live-region").textContent).toBe("Allowed. Ask Jarvis to try the task again.");
  });

  it("stays quiet for a grant the card never showed", () => {
    render(<PermissionPromptLayer />);

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "t1", { permissions: ["microphone"], feature: "voice", granted: true }, Date.now());
    });

    expect(screen.queryByTestId("permission-allowed-card")).toBeNull();
  });
});

describe("accessibility and layout", () => {
  it("announces the card in the polite live region when it opens, in the same node that later says 'Allowed'", async () => {
    render(<PermissionPromptLayer />);
    const live = screen.getByTestId("permission-live-region");
    expect(live.textContent).toBe("");

    open(needed());
    await screen.findByTestId("permission-prompt-card");

    expect(screen.getByTestId("permission-live-region")).toBe(live);
    expect(live.textContent).toContain("Access is turned off.");
    expect(live.textContent).toContain("turned off for Personal Jarvis");

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "t1", { permissions: ["microphone"], feature: "dictation", granted: true }, Date.now());
    });
    expect(screen.getByTestId("permission-live-region")).toBe(live);
    expect(live.textContent).toBe("Allowed. You can carry on.");
  });

  it("gives focus back to the element that had it when a button removes the card", async () => {
    const outside = document.createElement("textarea");
    document.body.appendChild(outside);
    outside.focus();
    render(<PermissionPromptLayer />);
    open(needed());
    const notNow = await screen.findByRole("button", { name: "Not now" });

    // Tab moves focus into the card; the card then removes itself under the focused button.
    act(() => notNow.focus());
    expect(document.activeElement).toBe(notNow);
    fireEvent.click(notNow);

    await waitFor(() => expect(screen.queryByTestId("permission-prompt-card")).toBeNull());
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("names the group by its heading, steals no focus and has no global Escape handler", async () => {
    const outside = document.createElement("textarea");
    document.body.appendChild(outside);
    outside.focus();
    render(<PermissionPromptLayer />);
    open(needed());
    const card = await screen.findByTestId("permission-prompt-card");

    const labelledBy = card.getAttribute("aria-labelledby")!;
    expect(document.getElementById(labelledBy)?.textContent).toBe("Access is turned off");
    expect(document.activeElement).toBe(outside);

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("permission-prompt-card")).not.toBeNull();
    outside.remove();
  });

  it("sits at z-[115] in the toast column and tells the toasts how far to move down", async () => {
    render(<PermissionPromptLayer />);
    open(needed());
    const layer = await screen.findByTestId("permission-prompt-layer");

    expect(layer.className).toContain("z-[115]");
    expect(layer.className).toContain("right-4");
    expect(layer.className).toContain("top-12");
    expect(document.documentElement.style.getPropertyValue(PERMISSION_CARD_OFFSET_VAR)).not.toBe("");
  });

  it("animates only where reduced motion allows", async () => {
    render(<PermissionPromptLayer />);
    open(needed());
    const card = await screen.findByTestId("permission-prompt-card");

    expect(card.className).toContain("motion-safe:animate-in");
    expect(card.className).not.toMatch(/(^|\s)animate-in/);
  });
});
