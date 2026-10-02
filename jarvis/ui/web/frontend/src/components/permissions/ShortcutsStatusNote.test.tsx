import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { isShortcutsNoteDismissed, shortcutsNoteKey } from "@/lib/shortcutsNoteDismissal";
import { usePermissionsStore } from "@/store/permissions";
import { ShortcutsStatusNote, shortcutsNoteMode } from "./ShortcutsStatusNote";

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let requestAnswer: Record<string, unknown> = {};

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
      if (url.includes("/request")) return { ok: true, status: 200, json: async () => requestAnswer } as Response;
      return { ok: true, status: 200, json: async () => ({ ok: true }) } as Response;
    }),
  );
}

function setEmbedded(on: boolean) {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = on;
}

function darwinSnapshot() {
  usePermissionsStore.setState({
    snapshot: {
      platform: "darwin",
      supported: true,
      headless: false,
      app_identity: { app_name: "Personal Jarvis", bundle_id: null, bundle_path: null, launched_as_bundle: true, stable: true },
      outside_installed_app: false,
      permissions: [],
      needed: [],
    },
  });
}

const NEEDS = { state: "needs_input_monitoring", detail: "English backend sentence" } as const;

beforeEach(() => {
  calls = [];
  window.localStorage.clear();
  requestAnswer = {
    permission: "input_monitoring",
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
  };
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {}, owner: true, snapshot: null, dictationNote: null });
  darwinSnapshot();
  setEmbedded(true);
  installFetch();
});
afterEach(() => {
  cleanup();
  setEmbedded(false);
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("shortcutsNoteMode", () => {
  const base = { mac: true, asked: null, osDialogOpen: false, grantedRecently: false };
  const answer = (outcome: string, extra: Record<string, unknown> = {}) =>
    ({ outcome, outside_installed_app: false, can_prompt: false, ...extra }) as never;

  it("says nothing without a status, off macOS, or when everything works", () => {
    expect(shortcutsNoteMode({ ...base, status: undefined })).toBe("hidden");
    expect(shortcutsNoteMode({ ...base, mac: false, status: NEEDS })).toBe("hidden");
    expect(shortcutsNoteMode({ ...base, status: { state: "ready", detail: "" } })).toBe("hidden");
  });

  it("asks for Input Monitoring in its own words, not as 'you denied'", () => {
    expect(shortcutsNoteMode({ ...base, status: NEEDS })).toBe("needs_permission");
  });

  it("follows what the Enable click found out", () => {
    expect(shortcutsNoteMode({ ...base, status: NEEDS, asked: answer("pending") })).toBe("asking");
    expect(shortcutsNoteMode({ ...base, status: NEEDS, osDialogOpen: true })).toBe("asking");
    expect(shortcutsNoteMode({ ...base, status: NEEDS, asked: answer("needs_settings") })).toBe("blocked");
    expect(shortcutsNoteMode({ ...base, status: NEEDS, asked: answer("denied") })).toBe("blocked");
  });

  it("asks the person to confirm the grantee when this is not the installed app", () => {
    expect(
      shortcutsNoteMode({
        ...base,
        status: NEEDS,
        asked: answer("needs_settings", { outside_installed_app: true, can_prompt: true }),
      }),
    ).toBe("outside");
  });

  it("reads a ready status with a detail as the restart hint, and a fresh grant as allowed", () => {
    expect(shortcutsNoteMode({ ...base, status: { state: "ready", detail: "Quit and reopen" } })).toBe("restart");
    expect(shortcutsNoteMode({ ...base, status: { state: "ready", detail: "" }, grantedRecently: true })).toBe("allowed");
  });

  it("says unavailable only on a Mac", () => {
    const status = { state: "unavailable_in_this_mode", detail: "" } as const;
    expect(shortcutsNoteMode({ ...base, status })).toBe("unavailable");
    expect(shortcutsNoteMode({ ...base, mac: false, status })).toBe("hidden");
  });
});

describe("ShortcutsStatusNote", () => {
  it("renders nothing and asks nothing when the shortcuts work", () => {
    render(<ShortcutsStatusNote surface="shortcuts" status={{ state: "ready", detail: "" }} />);
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
    expect(calls).toEqual([]);
  });

  it("renders nothing off macOS", () => {
    usePermissionsStore.setState({
      snapshot: { ...usePermissionsStore.getState().snapshot!, platform: "win32" },
    });
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });

  it("explains in a full sentence, never the backend's English, and never says 'denied'", () => {
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    const sentence = screen.getByTestId("shortcuts-status-sentence").textContent ?? "";
    expect(sentence).toContain("Shortcuts work in other apps once you allow Input Monitoring");
    expect(sentence).not.toContain("English backend sentence");
    expect(sentence.toLowerCase()).not.toContain("denied");
    expect(screen.getByRole("button", { name: "Enable global shortcuts" })).toBeTruthy();
  });

  it("asks macOS only from the Enable click, with the feature, then reads the status again", async () => {
    const onChanged = vi.fn();
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} onChanged={onChanged} />);
    expect(calls).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/permissions/input_monitoring/request?dry_run=false");
    expect(calls[0].body).toEqual({ feature: "global_shortcuts" });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // macOS is asking now: say so, no second button to press.
    await waitFor(() => expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("asking"));
    expect(screen.queryByRole("button", { name: "Enable global shortcuts" })).toBeNull();
  });

  it("leads to the Settings switch when macOS needs the person to flip it", async () => {
    requestAnswer = { ...requestAnswer, outcome: "needs_settings", reason: "needs_settings" };
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);

    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));

    await waitFor(() => expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("blocked"));
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toBe(
      "Switch on Personal Jarvis under Input Monitoring. The buttons and your voice keep working meanwhile.",
    );
    // ONE primary; "Check again" waits until the person came back from System Settings.
    expect(screen.queryByRole("button", { name: "Check again" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open System Settings" }));
    await waitFor(() =>
      expect(calls.some((call) => call.url.startsWith("/api/permissions/input_monitoring/open-settings"))).toBe(true),
    );
    expect(screen.queryByRole("button", { name: "Check again" })).toBeNull();

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(await screen.findByRole("button", { name: "Check again" }, { timeout: 2_000 })).toBeTruthy();
  });

  it("names this copy and asks again only after the person confirms (a .app outside Applications)", async () => {
    requestAnswer = {
      ...requestAnswer,
      outcome: "needs_settings",
      reason: "needs_settings",
      outside_installed_app: true,
      can_prompt: true,
    };
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);

    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));

    await waitFor(() => expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("outside"));
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toContain("running from outside your Applications folder");
    expect(calls).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Ask macOS now" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ feature: "global_shortcuts", allow_outside_app: true });
  });

  it("offers Quit and reopen when the tap is up but hears nothing", () => {
    render(<ShortcutsStatusNote surface="shortcuts" status={{ state: "ready", detail: "English restart hint" }} />);
    expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("restart");
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).not.toContain("English restart hint");
    expect(screen.getByRole("button", { name: "Quit and reopen" })).toBeTruthy();
  });

  it("explains without buttons in a remote browser", () => {
    setEmbedded(false);
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.getByTestId("shortcuts-status-sentence")).toBeTruthy();
    // Closing the note is not a host action, so it stays; nothing else is offered.
    expect(screen.queryAllByRole("button").map((button) => button.getAttribute("data-testid"))).toEqual([
      "shortcuts-status-dismiss",
    ]);
  });

  it("registers as the inline surface so the card does not repeat it", () => {
    const { unmount } = render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(usePermissionsStore.getState().inline).toEqual({ global_shortcuts: 1 });
    unmount();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("reads the status again when the grant arrives, and confirms it", async () => {
    const onChanged = vi.fn();
    const { rerender } = render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} onChanged={onChanged} />);

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "", { permissions: ["input_monitoring"], feature: "global_shortcuts", granted: true }, Date.now());
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());

    rerender(<ShortcutsStatusNote surface="shortcuts" status={{ state: "ready", detail: "" }} onChanged={onChanged} />);
    expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("allowed");
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toBe(
      "Input Monitoring allowed. Global shortcuts are on.",
    );
  });
});

describe("closing the note (Not now), remembered per surface", () => {
  it("every instance has a keyboard-reachable close button, and closing hides it and remembers it", () => {
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    const close = screen.getByRole("button", { name: "Dismiss" });
    expect(close.tagName).toBe("BUTTON");

    fireEvent.click(close);

    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
    expect(window.localStorage.getItem(shortcutsNoteKey("shortcuts"))).toBe("1");
    expect(isShortcutsNoteDismissed("shortcuts")).toBe(true);
  });

  it("stays closed on the next visit, whatever episode the permission is in", () => {
    window.localStorage.setItem(shortcutsNoteKey("shortcuts"), "1");
    const { rerender } = render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();

    // A different state of the same unfinished permission does not bring it back either.
    rerender(
      <ShortcutsStatusNote surface="shortcuts" status={{ state: "ready", detail: "English restart hint" }} />,
    );
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });

  it("keeps a key per surface: closing it on the Dictation page leaves the Shortcuts page alone", () => {
    const { unmount } = render(<ShortcutsStatusNote surface="dictation" variant="compact" status={NEEDS} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(window.localStorage.getItem(shortcutsNoteKey("dictation"))).toBe("1");
    expect(window.localStorage.getItem(shortcutsNoteKey("shortcuts"))).toBeNull();
    unmount();

    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.getByTestId("shortcuts-status-note")).toBeTruthy();
  });

  it("does not stand in for the floating card once it is closed", () => {
    window.localStorage.setItem(shortcutsNoteKey("shortcuts"), "1");
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("comes back after the shortcuts worked (status ready), so a later loss is explained again", () => {
    window.localStorage.setItem(shortcutsNoteKey("shortcuts"), "1");
    window.localStorage.setItem(shortcutsNoteKey("dictation"), "1");
    const { rerender } = render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();

    rerender(<ShortcutsStatusNote surface="shortcuts" status={{ state: "ready", detail: "" }} />);
    // Every surface's closing is forgotten, not only the one that saw the change.
    expect(window.localStorage.getItem(shortcutsNoteKey("shortcuts"))).toBeNull();
    expect(window.localStorage.getItem(shortcutsNoteKey("dictation"))).toBeNull();

    rerender(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    expect(screen.getByTestId("shortcuts-status-note")).toBeTruthy();
  });

  it("works with no storage at all: it still closes for this visit and never throws", () => {
    const broken = {
      getItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
      removeItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
    };
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", { configurable: true, value: broken });
    try {
      render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
      expect(screen.getByTestId("shortcuts-status-note")).toBeTruthy(); // unreadable store: not closed

      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

      expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });

  it("offers no close button on the transient answers (macOS asking, the allowed confirmation)", async () => {
    render(<ShortcutsStatusNote surface="shortcuts" status={NEEDS} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));
    await waitFor(() => expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("asking"));

    expect(screen.queryByTestId("shortcuts-status-dismiss")).toBeNull();
  });
});

describe("the compact variant (Dictation, Appshots)", () => {
  it("is one quiet line: a short sentence, one word of action, a close button", () => {
    render(<ShortcutsStatusNote surface="dictation" variant="compact" status={NEEDS} />);

    const note = screen.getByTestId("shortcuts-status-note");
    expect(note.getAttribute("data-variant")).toBe("compact");
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toBe(
      "Shortcuts need Input Monitoring to work in other apps.",
    );
    // The visible word is short, the accessible name is the full action.
    const enable = screen.getByRole("button", { name: "Enable global shortcuts" });
    expect(enable.textContent).toBe("Enable");
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeTruthy();
    expect(note.textContent).not.toContain("Buttons and voice work without it");
  });

  it("asks from the click like the full note", async () => {
    render(<ShortcutsStatusNote surface="appshots" variant="compact" status={NEEDS} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ feature: "global_shortcuts" });
  });

  it("keeps the full wording once it reports what the person just did", async () => {
    requestAnswer = { ...requestAnswer, outcome: "needs_settings", reason: "needs_settings" };
    render(<ShortcutsStatusNote surface="dictation" variant="compact" status={NEEDS} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));

    await waitFor(() => expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-mode")).toBe("blocked"));
    expect(screen.getByTestId("shortcuts-status-note").getAttribute("data-variant")).toBe("full");
    expect(screen.getByTestId("shortcuts-status-sentence").textContent).toContain("under Input Monitoring");
  });

  it("explains without an action in a remote browser, and can still be closed", () => {
    setEmbedded(false);
    render(<ShortcutsStatusNote surface="dictation" variant="compact" status={NEEDS} />);

    expect(screen.queryByRole("button", { name: "Enable global shortcuts" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });
});
