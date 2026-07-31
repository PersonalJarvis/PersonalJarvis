import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ViewHeader lives in ChatsView, which drags in the whole chat surface. The tab
// only needs the header's shape — and a testid so "renders no header when
// embedded" is assertable.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => (
    <header data-testid="view-header">{title}</header>
  ),
}));

import { ShortcutsTab } from "@/views/voice/ShortcutsTab";

const KEYBINDS = {
  call: "f3+f4",
  hangup: "f1+f2",
  dictate: "ctrl+right_alt+j",
  dictate_toggle: "ctrl+right_alt+space",
  paste_last: "ctrl+alt+v",
};

const CONFIG = {
  keybinds: KEYBINDS,
  defaults: { ...KEYBINDS },
  suggestions: ["ctrl+shift+space", "ctrl+shift+d"],
  restart_required: false,
};

/** GET /api/dictation/status — only the slice this tab reads. */
const STATUS = {
  mode: "hold",
  insertion: { can_insert: true, reason: "", detail: "" },
};

/**
 * Route-aware fetch stub; returns the recorded calls for assertions.
 *
 * `status` overrides the dictation-status slice, which drives the two notices
 * (push-to-talk silently on toggle, insertion impossible on this host).
 */
function stubFetch(status: Record<string, unknown> | null = STATUS) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(init.body as string) : null,
    });
    if (url === "/api/settings/keybinds" && method === "PUT") {
      return {
        ok: true,
        json: async () => ({
          ok: true,
          action: "dictate",
          hotkey: "ctrl+shift+space",
          persisted: true,
          restart_required: false,
        }),
      };
    }
    if (url === "/api/dictation/status") {
      if (status === null) return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, json: async () => status };
    }
    if (url === "/api/dictation/settings") {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: true, json: async () => CONFIG };
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/** The row's combo field text with whitespace collapsed ("Ctrl+AltGr+J"). */
function comboText(action: string): string {
  return (
    screen.getByTestId(`combo-field-${action}`).textContent?.replace(/\s+/g, "") ??
    ""
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ShortcutsTab", () => {
  it("shows all three dictation shortcuts with their current combos", async () => {
    stubFetch();
    render(<ShortcutsTab />);

    // Every key that has to do with dictation lives on this ONE surface:
    // push-to-talk (`dictate`), hands-free (`dictate_toggle`) and re-inserting
    // the last transcript (`paste_last`).
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));
    expect(comboText("dictate_toggle")).toBe("Ctrl+AltGr+Space");
    expect(comboText("paste_last")).toBe("Ctrl+Alt+V");
    expect(screen.getByText("Push to talk")).toBeTruthy();
    expect(screen.getByText("Hands-free")).toBeTruthy();
    expect(screen.getByText("Paste again")).toBeTruthy();
    // Call and Hangup are NOT editable here — this tab is about dictation.
    expect(screen.queryByTestId("combo-field-call")).toBeNull();
  });

  it("says up front when this host cannot paste into another app", async () => {
    stubFetch({ mode: "hold", insertion: { can_insert: false } });
    render(<ShortcutsTab />);

    // Wayland / headless / an elevated window in front: the key still works,
    // but the text lands on the clipboard. Saying so beats letting the user
    // discover it by pressing a key that appears to do nothing.
    const line = await waitFor(() =>
      screen.getByTestId("shortcuts-paste-last-blocked"),
    );
    expect(line.textContent).toMatch(/clipboard/i);
  });

  it("keeps the paste caveat away while insertion works", async () => {
    stubFetch();
    render(<ShortcutsTab />);

    await waitFor(() => expect(comboText("paste_last")).toBe("Ctrl+Alt+V"));
    expect(screen.queryByTestId("shortcuts-paste-last-blocked")).toBeNull();
  });

  it("offers a way back when push-to-talk is secretly in toggle mode", async () => {
    const calls = stubFetch({ mode: "toggle", insertion: { can_insert: true } });
    render(<ShortcutsTab />);

    // The old "Key behaviour" dropdown is gone, so without this notice an
    // install already on "toggle" would have a push-to-talk key that toggles
    // and no surface anywhere to say so, let alone fix it.
    await waitFor(() => expect(screen.getByTestId("shortcuts-mode-notice")).toBeTruthy());
    fireEvent.click(screen.getByTestId("shortcuts-mode-fix"));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url === "/api/dictation/settings" &&
            c.method === "PUT" &&
            (c.body as { mode: string }).mode === "hold",
        ),
      ).toBe(true),
    );
  });

  it("never accuses the user of a mode a silent backend did not report", async () => {
    stubFetch(null);
    render(<ShortcutsTab />);

    // A 503 from the status route means "unknown", not "toggle" — and unknown
    // must render neither the accusation nor the paste caveat.
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));
    expect(screen.queryByTestId("shortcuts-mode-notice")).toBeNull();
    expect(screen.queryByTestId("shortcuts-paste-last-blocked")).toBeNull();
  });

  it("renders its own header standalone and stands it down when embedded", async () => {
    stubFetch();
    const { rerender } = render(<ShortcutsTab />);
    await waitFor(() => expect(screen.getByTestId("view-header")).toBeTruthy());

    rerender(<ShortcutsTab hideHeader />);
    expect(screen.queryByTestId("view-header")).toBeNull();
  });

  it("picking push-to-talk saves it and pins the dictation mode to hold", async () => {
    const calls = stubFetch();
    render(<ShortcutsTab />);
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));

    // One click on a suggested combo IS the change — there is no Save step.
    fireEvent.click(screen.getByTestId("suggestion-dictate-ctrl+shift+space"));
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+Shift+Space"));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url === "/api/settings/keybinds" &&
            c.method === "PUT" &&
            (c.body as { action: string }).action === "dictate",
        ),
      ).toBe(true),
    );
    // "Push to talk" must MEAN hold — a user left on [dictation].mode="toggle"
    // would otherwise hold the keys and get toggle behaviour.
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url === "/api/dictation/settings" &&
            c.method === "PUT" &&
            (c.body as { mode: string }).mode === "hold",
        ),
      ).toBe(true),
    );
  });

  it("does not pin the mode when the hands-free row is saved", async () => {
    const calls = stubFetch();
    render(<ShortcutsTab />);
    await waitFor(() => expect(comboText("dictate_toggle")).toBe("Ctrl+AltGr+Space"));

    fireEvent.click(
      screen.getByTestId("suggestion-dictate_toggle-ctrl+shift+d"),
    );
    await waitFor(() => expect(comboText("dictate_toggle")).toBe("Ctrl+Shift+D"));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT")).toBe(true),
    );
    expect(calls.some((c) => c.url === "/api/dictation/settings")).toBe(false);
  });

  it("opens the on-screen keyboard and flags keys other actions already own", async () => {
    stubFetch();
    render(<ShortcutsTab />);
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));

    fireEvent.click(screen.getByTestId("record-keybind-dictate"));

    // Call (f3+f4) and Hangup (f1+f2) are bound elsewhere; the map must say so
    // here too, or the user picks a key that cannot be saved.
    const f3 = await waitFor(() => screen.getByTestId("key-F3"));
    expect(f3.getAttribute("title")).toMatch(/call/i);
    expect(screen.getByTestId("key-F1").getAttribute("title")).toMatch(/hangup/i);
    // The hands-free row's own keys are flagged for the push-to-talk row too.
    expect(screen.getByTestId("key-Space").getAttribute("title")).toMatch(
      /hands-free/i,
    );
  });

  it("never auto-saves an overlapping combo, and says why", async () => {
    vi.useFakeTimers();
    try {
      const calls = stubFetch();
      render(<ShortcutsTab />);
      await vi.waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));

      // Build hangup's exact combo on the push-to-talk row: F1+F2.
      fireEvent.click(screen.getByTestId("record-keybind-dictate"));
      fireEvent.click(screen.getByTestId("key-ControlLeft"));
      fireEvent.click(screen.getByTestId("key-AltRight"));
      fireEvent.click(screen.getByTestId("key-KeyJ"));
      fireEvent.click(screen.getByTestId("key-F1"));
      fireEvent.click(screen.getByTestId("key-F2"));

      const line = await vi.waitFor(() =>
        screen.getByTestId("keybind-validation-dictate"),
      );
      // The collision names the other action the way the UI labels it, not by
      // its raw id.
      expect(line.textContent).toMatch(/hangup/i);

      // With no Save button to disable, THIS is what "blocked" means now: the
      // settle timer fires and the request is never sent. The inline sentence
      // above is the only feedback there is, which is why it has to be there.
      await act(async () => {
        vi.advanceTimersByTime(5_000);
      });
      expect(calls.some((c) => c.method === "PUT")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
