import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
};

const CONFIG = {
  keybinds: KEYBINDS,
  defaults: { ...KEYBINDS },
  suggestions: ["ctrl+shift+space", "ctrl+shift+d"],
  restart_required: false,
};

/** Route-aware fetch stub; returns the recorded calls for assertions. */
function stubFetch() {
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
  it("shows both dictation shortcuts with their current combos", async () => {
    stubFetch();
    render(<ShortcutsTab />);

    // Push-to-talk is the `dictate` action, hands-free the new
    // `dictate_toggle` — two rows, one per way of starting dictation.
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));
    expect(comboText("dictate_toggle")).toBe("Ctrl+AltGr+Space");
    expect(screen.getByText("Push to talk")).toBeTruthy();
    expect(screen.getByText("Hands-free")).toBeTruthy();
    // Call and Hangup are NOT editable here — this tab is about dictation.
    expect(screen.queryByTestId("combo-field-call")).toBeNull();
  });

  it("renders its own header standalone and stands it down when embedded", async () => {
    stubFetch();
    const { rerender } = render(<ShortcutsTab />);
    await waitFor(() => expect(screen.getByTestId("view-header")).toBeTruthy());

    rerender(<ShortcutsTab hideHeader />);
    expect(screen.queryByTestId("view-header")).toBeNull();
  });

  it("saving push-to-talk also pins the dictation mode to hold", async () => {
    const calls = stubFetch();
    render(<ShortcutsTab />);
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));

    // A suggested combo is one click away; Save appears once the row is dirty.
    fireEvent.click(screen.getByTestId("suggestion-dictate-ctrl+shift+space"));
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+Shift+Space"));
    fireEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

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
    fireEvent.click(screen.getAllByRole("button", { name: /^save$/i })[0]);

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

  it("blocks an overlapping combo before it can be saved", async () => {
    stubFetch();
    render(<ShortcutsTab />);
    await waitFor(() => expect(comboText("dictate")).toBe("Ctrl+AltGr+J"));

    // Build hangup's exact combo on the push-to-talk row: F1+F2.
    fireEvent.click(screen.getByTestId("record-keybind-dictate"));
    fireEvent.click(screen.getByTestId("key-ControlLeft"));
    fireEvent.click(screen.getByTestId("key-AltRight"));
    fireEvent.click(screen.getByTestId("key-KeyJ"));
    fireEvent.click(screen.getByTestId("key-F1"));
    fireEvent.click(screen.getByTestId("key-F2"));

    const line = await waitFor(() =>
      screen.getByTestId("keybind-validation-dictate"),
    );
    // The collision names the other action the way the UI labels it, not by
    // its raw id.
    expect(line.textContent).toMatch(/hangup/i);
    const save = screen.getAllByRole("button", {
      name: /^save$/i,
    })[0] as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });
});
