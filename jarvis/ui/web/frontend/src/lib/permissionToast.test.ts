import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { translate, useI18nStore, type UiLanguage } from "@/i18n";
import {
  PERMISSION_TOAST_REPEAT_COOLDOWN_MS,
  PERMISSION_TOAST_TTL_MS,
  handlePermissionToastEvent,
  planPermissionToast,
  resetPermissionToastState,
} from "@/lib/permissionToast";
import { useEventStore } from "@/store/events";

const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";

function episode(over: Record<string, unknown> = {}) {
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
    detail: "English support sentence",
    ...over,
  };
}

describe("planPermissionToast", () => {
  it("tells a denied permission with one sentence and Open System Settings", () => {
    expect(planPermissionToast(episode())).toMatchObject({
      variant: "needs",
      messageKey: "permissions.toast.microphone",
      action: "open_settings",
      permission: "microphone",
      feature: "dictation",
    });
  });

  it.each([
    ["microphone", "permissions.toast.microphone"],
    ["screen_recording", "permissions.toast.screen_recording"],
    ["accessibility", "permissions.toast.accessibility"],
    ["input_monitoring", "permissions.toast.input_monitoring"],
    ["automation", "permissions.toast.automation"],
    ["credential_store", "permissions.toast.generic"],
  ])("has one sentence for %s", (permission, key) => {
    expect(planPermissionToast(episode({ permissions: [permission] }))?.messageKey).toBe(key);
  });

  it("folds event_posting into accessibility (one pane)", () => {
    expect(planPermissionToast(episode({ permissions: ["event_posting"] }))).toMatchObject({
      messageKey: "permissions.toast.accessibility",
      permission: "accessibility",
    });
  });

  it("names the first missing permission when an episode needs two", () => {
    const plan = planPermissionToast(episode({ permissions: ["screen_recording", "accessibility"], feature: "computer_use" }));
    expect(plan?.permission).toBe("screen_recording");
  });

  it("offers Ask macOS now when macOS can still ask, and says what an outside run means", () => {
    expect(planPermissionToast(episode({ reason: "not_determined", can_prompt: true }))).toMatchObject({
      action: "ask_now",
      messageKey: "permissions.toast.microphone",
    });
    expect(
      planPermissionToast(episode({ reason: "needs_settings", can_prompt: true, outside_app: true })),
    ).toMatchObject({ action: "ask_outside", messageKey: "permissions.toast.outside" });
  });

  it("offers a restart for an allowed permission whose use failed", () => {
    expect(
      planPermissionToast(episode({ permissions: ["screen_recording"], reason: "restart_hint" })),
    ).toMatchObject({ variant: "restart", action: "restart", messageKey: "permissions.toast.restart" });
  });

  it("explains a restriction without a button, and stays quiet when nothing can be asked", () => {
    expect(planPermissionToast(episode({ reason: "restricted", can_open_settings: false }))).toMatchObject({
      variant: "restricted",
      action: null,
    });
    expect(planPermissionToast(episode({ reason: "unavailable" }))).toBeNull();
  });

  it("offers no button when there is no pane to open", () => {
    expect(planPermissionToast(episode({ can_open_settings: false }))?.action).toBeNull();
  });

  it("never toasts while macOS is asking itself", () => {
    expect(planPermissionToast(episode({ phase: "os_dialog", reason: "not_determined" }))).toBeNull();
  });

  it("never toasts for a background consumer", () => {
    expect(planPermissionToast(episode({ origin: "background" }))).toBeNull();
    expect(planPermissionToast(episode({ origin: "background", feature: "screen_context" }))).toBeNull();
  });

  it("makes ONE exception: a denied microphone for the wake word, once per session", () => {
    const wake = episode({ origin: "background", feature: "wake_word" });
    expect(planPermissionToast(wake)).toMatchObject({ variant: "wake_word", action: "open_settings" });
    expect(planPermissionToast(wake, { wakeWordToldThisSession: true })).toBeNull();
    // Only "denied": a wake word that merely was not asked yet stays quiet.
    expect(planPermissionToast({ ...wake, reason: "not_determined", can_prompt: true })).toBeNull();
  });

  it("ignores payloads it cannot read honestly", () => {
    expect(planPermissionToast(null)).toBeNull();
    expect(planPermissionToast({})).toBeNull();
    expect(planPermissionToast(episode({ permissions: [] }))).toBeNull();
    expect(planPermissionToast(episode({ reason: "from_the_future" }))).toBeNull();
  });

  it("keys an episode by feature, permissions and reason", () => {
    const a = planPermissionToast(episode());
    const b = planPermissionToast(episode({ reason: "needs_settings" }));
    expect(a?.key).not.toBe(b?.key);
    expect(a?.episode).toBe(b?.episode);
  });
});

describe("the copy", () => {
  afterEach(() => useI18nStore.getState().setUi("en", { push: false }));

  const SHAPES = [
    episode(),
    episode({ permissions: ["screen_recording"] }),
    episode({ permissions: ["accessibility"] }),
    episode({ permissions: ["input_monitoring"] }),
    episode({ permissions: ["automation"], target: "com.spotify.client" }),
    episode({ permissions: ["credential_store"] }),
    episode({ reason: "restricted" }),
    episode({ reason: "restart_hint", permissions: ["screen_recording"] }),
    episode({ reason: "needs_settings", can_prompt: true, outside_app: true }),
    episode({ origin: "background", feature: "wake_word" }),
  ];

  it.each(["en", "de", "es"] as UiLanguage[])("every sentence, name and button exists in %s", (language) => {
    useI18nStore.getState().setUi(language, { push: false });
    for (const shape of SHAPES) {
      const plan = planPermissionToast(shape)!;
      expect(translate(plan.messageKey), plan.messageKey).not.toBe(plan.messageKey);
      if (plan.nameKey) expect(translate(plan.nameKey), plan.nameKey).not.toBe(plan.nameKey);
    }
    for (const key of [
      "permissions.toast.action.open_settings",
      "permissions.toast.action.ask_now",
      "permissions.toast.action.restart",
      "permissions.toast.action_failed",
      "permissions.restart_failed",
    ]) {
      expect(translate(key), key).not.toBe(key);
    }
  });

  it("keeps the sentences short, calm and free of pane names and jargon", () => {
    for (const language of ["en", "de", "es"] as UiLanguage[]) {
      useI18nStore.getState().setUi(language, { push: false });
      for (const shape of SHAPES) {
        const plan = planPermissionToast(shape)!;
        const text = translate(plan.messageKey);
        expect(text.length, text).toBeLessThan(170);
        expect(text).not.toMatch(/Privacy & Security|Privacidad y seguridad|Datenschutz & Sicherheit|TCC|tccutil|[>]/);
      }
    }
  });
});

describe("handlePermissionToastEvent", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  const restart = vi.fn(async () => undefined);

  function onMac(embedded = true) {
    if (embedded) (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(MAC_UA);
  }

  const handle = (name: string, payload: unknown) => handlePermissionToastEvent(name, payload, { restart });

  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ toasts: [], solo: false });
    resetPermissionToastState();
    restart.mockClear();
    fetchMock = vi.fn(async () => ({ ok: true, status: 200 }) as Response);
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
  });

  function toast() {
    const toasts = useEventStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    return toasts[0];
  }

  it("pushes the sentence with its button and a lifetime long enough to use it", () => {
    onMac();
    const now = Date.now();
    handle("PermissionNeeded", episode());

    const t = toast();
    expect(t.message).toBe("Personal Jarvis needs microphone access to hear you.");
    expect(t.kind).toBe("warning");
    expect(t.action?.label).toBe("Open System Settings");
    expect(t.expiresAt - now).toBeGreaterThanOrEqual(PERMISSION_TOAST_TTL_MS - 50);
  });

  it("Open System Settings posts to the pane's open-settings route", async () => {
    onMac();
    handle("PermissionNeeded", episode({ permissions: ["screen_recording"], feature: "screen_context" }));

    await toast().action!.onAction();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/permissions/screen_recording/open-settings?dry_run=false");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body).toBeUndefined();
  });

  it("Ask macOS now posts allow_outside_app only for an outside run", async () => {
    onMac();
    handle("PermissionNeeded", episode({ reason: "needs_settings", can_prompt: true, outside_app: true }));

    const t = toast();
    expect(t.action?.label).toBe("Ask macOS now");
    await t.action!.onAction();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/permissions/microphone/request?dry_run=false");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      feature: "dictation",
      allow_outside_app: true,
    });
  });

  it("Ask macOS now inside the installed app confirms nothing about another app", async () => {
    onMac();
    handle("PermissionNeeded", episode({ reason: "not_determined", can_prompt: true }));

    await toast().action!.onAction();

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ feature: "dictation" });
  });

  it("an Automation ask names the player the episode is about", async () => {
    onMac();
    handle(
      "PermissionNeeded",
      episode({
        permissions: ["automation"],
        feature: "audio_ducking",
        reason: "not_determined",
        can_prompt: true,
        target: "com.spotify.client",
      }),
    );

    await toast().action!.onAction();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/permissions/automation/request?dry_run=false");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      feature: "audio_ducking",
      target: "com.spotify.client",
    });
  });

  it("the restart variant restarts through the injected restart and stays up for a second press", async () => {
    onMac();
    handle("PermissionNeeded", episode({ permissions: ["screen_recording"], reason: "restart_hint" }));

    const t = toast();
    expect(t.message).toBe("Access to screen recording is on, but it only works after Personal Jarvis restarts.");
    expect(t.action).toMatchObject({ label: "Quit and reopen", keepOpen: true });
    await t.action!.onAction();
    expect(restart).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a button that fails says so, in one existing error toast", async () => {
    onMac();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    fetchMock.mockResolvedValue({ ok: false, status: 429 } as Response);
    handle("PermissionNeeded", episode());

    await toast().action!.onAction();

    const messages = useEventStore.getState().toasts.map((t) => t.message);
    expect(messages).toContain("That did not work. Please open System Settings yourself.");
  });

  it("is quiet outside the owner window: detached window, plain browser, other OS", () => {
    onMac();
    useEventStore.setState({ solo: true });
    handle("PermissionNeeded", episode());
    expect(useEventStore.getState().toasts).toEqual([]);

    useEventStore.setState({ solo: false });
    vi.restoreAllMocks();
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    handle("PermissionNeeded", episode());
    expect(useEventStore.getState().toasts).toEqual([]);

    (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
    handle("PermissionNeeded", episode());
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("tells the wake word's denied microphone once per session, whoever started it", () => {
    onMac();
    const wake = episode({ origin: "background", feature: "wake_word" });
    handle("PermissionNeeded", wake);
    expect(toast().message).toContain("The wake word cannot listen");

    useEventStore.setState({ toasts: [] });
    handle("PermissionResolved", { permissions: ["microphone"], feature: "wake_word", granted: false });
    handle("PermissionNeeded", wake);
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("does not repeat an episode, but a new reason of the same episode is news", () => {
    onMac();
    handle("PermissionNeeded", episode({ reason: "not_determined", can_prompt: true }));
    handle("PermissionNeeded", episode({ reason: "not_determined", can_prompt: true }));
    expect(useEventStore.getState().toasts).toHaveLength(1);

    handle("PermissionNeeded", episode());
    expect(useEventStore.getState().toasts).toHaveLength(2);
  });

  describe("a toast that is gone", () => {
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-01-01T10:00:00Z"));
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("is told again on the next press, once the cooldown has passed", () => {
      onMac();
      handle("PermissionNeeded", episode());
      expect(toast().message).toContain("microphone");

      // Dismissed (or expired) and the person presses the feature again.
      useEventStore.setState({ toasts: [] });
      vi.setSystemTime(Date.now() + PERMISSION_TOAST_REPEAT_COOLDOWN_MS + 1);
      handle("PermissionNeeded", episode());
      expect(useEventStore.getState().toasts).toHaveLength(1);
    });

    it("is not told twice in a burst, even though its toast is already gone", () => {
      onMac();
      handle("PermissionNeeded", episode());
      useEventStore.setState({ toasts: [] });
      vi.setSystemTime(Date.now() + PERMISSION_TOAST_REPEAT_COOLDOWN_MS - 1);
      handle("PermissionNeeded", episode());
      expect(useEventStore.getState().toasts).toEqual([]);
    });

    it("is not told again while its toast is still on screen", () => {
      onMac();
      handle("PermissionNeeded", episode());
      const shown = useEventStore.getState().toasts.map((t) => t.id);
      vi.setSystemTime(Date.now() + PERMISSION_TOAST_REPEAT_COOLDOWN_MS * 3);
      handle("PermissionNeeded", episode());
      expect(useEventStore.getState().toasts.map((t) => t.id)).toEqual(shown);
    });

    it("never repeats the wake word's toast within a session", () => {
      onMac();
      const wake = episode({ origin: "background", feature: "wake_word" });
      handle("PermissionNeeded", wake);
      useEventStore.setState({ toasts: [] });
      vi.setSystemTime(Date.now() + PERMISSION_TOAST_REPEAT_COOLDOWN_MS * 10);
      handle("PermissionNeeded", wake);
      expect(useEventStore.getState().toasts).toEqual([]);
    });
  });

  it("forgets only the episode a PermissionResolved names", () => {
    onMac();
    handle("PermissionNeeded", episode());
    handle("PermissionNeeded", episode({ feature: "voice" }));
    expect(useEventStore.getState().toasts).toHaveLength(1); // same sentence collapses into one

    handle("PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: false });
    useEventStore.setState({ toasts: [] });
    handle("PermissionNeeded", episode({ feature: "voice" }));
    expect(useEventStore.getState().toasts).toEqual([]); // still told
    handle("PermissionNeeded", episode());
    expect(useEventStore.getState().toasts).toHaveLength(1); // told again
  });

  it("ignores every other event", () => {
    onMac();
    handle("SystemStateChanged", episode());
    handle("PermissionResolved", null);
    expect(useEventStore.getState().toasts).toEqual([]);
  });
});
