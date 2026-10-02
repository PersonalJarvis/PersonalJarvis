import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_PROMPTS, reducePermissionEvent } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";
import { useSettingsJump } from "@/store/settingsJump";
import { InlinePermissionNote } from "./InlinePermissionNote";

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      return { ok: true, status: 200, json: async () => ({ ok: true, status: "denied", id: "microphone" }) } as Response;
    }),
  );
}

function needed(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "wake_word",
    reason: "denied",
    phase: "blocked",
    origin: "background",
    target: "",
    can_prompt: false,
    can_open_settings: true,
    outside_app: false,
    detail: "ENGLISH BACKEND DETAIL",
    ...overrides,
  };
}

function publish(event: "PermissionNeeded" | "PermissionResolved", payload: Record<string, unknown>) {
  act(() => {
    usePermissionsStore.getState().ingest(event, "", payload, Date.now());
  });
}

function setEmbedded(on: boolean) {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = on;
}

beforeEach(() => {
  calls = [];
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {}, owner: true, snapshot: null, dictationNote: null });
  setEmbedded(true);
  installFetch();
});
afterEach(() => {
  cleanup();
  setEmbedded(false);
  vi.unstubAllGlobals();
});

describe("InlinePermissionNote", () => {
  it("renders nothing while the feature has no episode", () => {
    render(<InlinePermissionNote feature="wake_word" allowedKey="permissions.inline.wake_word.allowed" />);
    expect(screen.queryByTestId("inline-permission-note")).toBeNull();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("says a blocked microphone as one full sentence from i18n, never the backend's English", () => {
    usePermissionsStore.setState({ ...reducePermissionEvent(EMPTY_PROMPTS, "PermissionNeeded", "", needed(), 1)! });
    render(<InlinePermissionNote feature="wake_word" />);

    const sentence = screen.getByTestId("inline-permission-sentence").textContent ?? "";
    expect(sentence).toBe(
      "The wake word cannot work because access to “Microphone” is turned off for Personal Jarvis. Turn it on in System Settings, then come back.",
    );
    expect(screen.getByTestId("inline-permission-note").textContent).not.toContain("BACKEND DETAIL");
    expect(screen.getByTestId("inline-permission-note").getAttribute("data-phase")).toBe("blocked");
  });

  it("links to Settings > Privacy in the embedded window, and only there", () => {
    usePermissionsStore.setState({ ...reducePermissionEvent(EMPTY_PROMPTS, "PermissionNeeded", "", needed(), 1)! });
    const { unmount } = render(<InlinePermissionNote feature="wake_word" />);

    fireEvent.click(screen.getByRole("button", { name: "See all permissions" }));
    expect(useSettingsJump.getState().target).toBe("permissions");
    expect(useEventStore.getState().activeSection).toBe("settings");
    useSettingsJump.getState().take();
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
    unmount();

    // A remote browser gets the sentence without any host-only button or link.
    setEmbedded(false);
    render(<InlinePermissionNote feature="wake_word" />);
    expect(screen.queryByRole("button", { name: "See all permissions" })).toBeNull();
  });

  it("names the app from app_identity.app_name", () => {
    usePermissionsStore.setState({
      ...reducePermissionEvent(EMPTY_PROMPTS, "PermissionNeeded", "", needed(), 1)!,
      snapshot: {
        platform: "darwin",
        supported: true,
        headless: false,
        app_identity: { app_name: "Acme Voice", bundle_id: null, bundle_path: null, launched_as_bundle: true, stable: true },
        outside_installed_app: false,
        permissions: [],
        needed: [],
      },
    });
    render(<InlinePermissionNote feature="wake_word" />);
    expect(screen.getByTestId("inline-permission-sentence").textContent).toContain("turned off for Acme Voice");
  });

  it("registers as the feature's inline surface only while it shows something (the card stays quiet)", () => {
    const { unmount } = render(<InlinePermissionNote feature="wake_word" />);
    expect(usePermissionsStore.getState().inline).toEqual({});

    publish("PermissionNeeded", needed());
    expect(usePermissionsStore.getState().inline).toEqual({ wake_word: 1 });

    unmount();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("shows macOS asking without buttons while the OS dialog is open", () => {
    publish("PermissionNeeded", needed({ phase: "os_dialog", reason: "not_determined" }));
    render(<InlinePermissionNote feature="wake_word" />);

    expect(screen.getByTestId("inline-permission-note").getAttribute("data-phase")).toBe("os_dialog");
    expect(screen.getByTestId("inline-permission-sentence").textContent).toBe(
      "macOS is asking for access to “Microphone”. Answer its question to continue.",
    );
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("offers Open System Settings and Check again, and opens the pane from the click", async () => {
    publish("PermissionNeeded", needed());
    render(<InlinePermissionNote feature="wake_word" />);

    const open = screen.getByRole("button", { name: "Open System Settings" });
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
    // An inline note is passive status: no "Not now", no "Reset".
    expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();

    fireEvent.click(open);
    await waitFor(() =>
      expect(calls.some((call) => call.url.startsWith("/api/permissions/microphone/open-settings") && call.method === "POST")).toBe(true),
    );
  });

  it("asks macOS from the Continue click with the feature attached, never before", async () => {
    publish("PermissionNeeded", needed({ reason: "not_determined", can_prompt: true }));
    render(<InlinePermissionNote feature="wake_word" />);
    expect(calls).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("/api/permissions/microphone/request?dry_run=false");
    expect(calls[0].body).toEqual({ feature: "wake_word" });
  });

  it("outside the installed app the note names the launcher and the button is the ask", async () => {
    publish("PermissionNeeded", needed({ reason: "needs_settings", can_prompt: true, outside_app: true, origin: "user" }));
    render(<InlinePermissionNote feature="wake_word" />);

    const sentence = screen.getByTestId("inline-permission-sentence").textContent ?? "";
    expect(sentence).toBe(
      "Personal Jarvis was started from another app, such as your terminal, so macOS would record access to “Microphone” for that app and for everything you run in it. The button asks macOS now.",
    );
    expect(sentence).not.toMatch(/Continue and macOS will ask you|Switch it on for/);
    expect(calls).toEqual([]); // the sentence alone asks nothing

    fireEvent.click(screen.getByRole("button", { name: "Allow for the app that started Personal Jarvis" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ feature: "wake_word", allow_outside_app: true });
  });

  it("a route answer the surface already holds gets the same outside sentence", () => {
    render(
      <InlinePermissionNote
        feature="wake_word"
        local={{ permissions: ["microphone"], reason: "not_determined", can_prompt: true, outside_app: true }}
      />,
    );

    expect(screen.getByTestId("inline-permission-sentence").textContent).toMatch(
      /^Personal Jarvis was started from another app/,
    );
  });

  it("a .app run from a disk image names this copy, never a terminal, and asks macOS", async () => {
    usePermissionsStore.setState({
      snapshot: {
        platform: "darwin",
        supported: true,
        headless: false,
        app_identity: { app_name: "Personal Jarvis", bundle_id: "x", bundle_path: "/Volumes/J/J.app", launched_as_bundle: true, stable: true },
        outside_installed_app: true,
        permissions: [],
        needed: [],
      },
    });
    publish("PermissionNeeded", needed({ reason: "needs_settings", can_prompt: true, outside_app: true, origin: "user" }));
    render(<InlinePermissionNote feature="wake_word" />);

    const sentence = screen.getByTestId("inline-permission-sentence").textContent ?? "";
    expect(sentence).toContain("running from outside your Applications folder");
    expect(sentence).not.toMatch(/terminal/i);

    fireEvent.click(screen.getByRole("button", { name: "Ask macOS now" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ feature: "wake_word", allow_outside_app: true });
  });

  it("offers no host-only action to a remote browser", () => {
    setEmbedded(false);
    publish("PermissionNeeded", needed());
    render(<InlinePermissionNote feature="wake_word" />);

    expect(screen.getByTestId("inline-permission-sentence")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("restricted and unavailable explain only", () => {
    publish("PermissionNeeded", needed({ reason: "restricted", can_open_settings: false }));
    render(<InlinePermissionNote feature="wake_word" />);
    expect(screen.getByTestId("inline-permission-sentence").textContent).toContain("restricts it");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("says 'allowed' after a grant, then goes away", async () => {
    publish("PermissionNeeded", needed());
    render(
      <InlinePermissionNote feature="wake_word" allowedKey="permissions.inline.wake_word.allowed" holdMs={60} />,
    );
    expect(screen.getByTestId("inline-permission-note").getAttribute("data-phase")).toBe("blocked");

    publish("PermissionResolved", { permissions: ["microphone"], feature: "wake_word", granted: true });

    expect(screen.getByTestId("inline-permission-note").getAttribute("data-phase")).toBe("allowed");
    expect(screen.getByTestId("inline-permission-sentence").textContent).toBe(
      "Microphone allowed. The wake word can listen now.",
    );
    await waitFor(() => expect(screen.queryByTestId("inline-permission-note")).toBeNull(), { timeout: 1500 });
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("does not claim 'allowed' when the surface did not ask (showAllowed false)", () => {
    render(
      <InlinePermissionNote
        feature="dictation"
        allowedKey="permissions.inline.dictation.allowed"
        showAllowed={false}
      />,
    );
    publish("PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true });
    expect(screen.queryByTestId("inline-permission-note")).toBeNull();
  });

  it("falls back to a route answer the surface holds, and the store's episode wins over it", () => {
    render(
      <InlinePermissionNote
        feature="wake_word"
        local={{ permissions: ["microphone"], reason: "needs_settings", can_open_settings: true }}
      />,
    );
    expect(screen.getByTestId("inline-permission-note").getAttribute("data-reason")).toBe("needs_settings");

    publish("PermissionNeeded", needed({ reason: "restricted", can_open_settings: false }));
    expect(screen.getByTestId("inline-permission-note").getAttribute("data-reason")).toBe("restricted");
  });

  it("can be hidden by its surface and then neither renders nor registers", () => {
    publish("PermissionNeeded", needed());
    render(<InlinePermissionNote feature="wake_word" hidden />);
    expect(screen.queryByTestId("inline-permission-note")).toBeNull();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("closes through its own button when the surface floats over other content", () => {
    publish("PermissionNeeded", needed({ feature: "dictation", origin: "user" }));
    const onDismiss = vi.fn();
    render(<InlinePermissionNote feature="dictation" onDismiss={onDismiss} />);

    fireEvent.click(screen.getByTestId("inline-permission-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("is a polite live region and never takes focus", () => {
    publish("PermissionNeeded", needed());
    render(<InlinePermissionNote feature="wake_word" />);
    const note = screen.getByTestId("inline-permission-note");
    expect(note.getAttribute("role")).toBe("status");
    expect(note.getAttribute("aria-live")).toBe("polite");
    expect(document.activeElement === document.body || !note.contains(document.activeElement)).toBe(true);
  });
});
