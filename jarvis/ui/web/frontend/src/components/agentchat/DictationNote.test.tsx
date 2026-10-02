import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DictationButton } from "@/components/agentchat/DictationButton";
import { dictationRefusalKey } from "@/components/agentchat/DictationNote";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";

const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15";

interface Call {
  url: string;
  method: string;
}
let calls: Call[] = [];
let shortcutsState = "ready";

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/settings/keybinds") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ shortcuts_status: { state: shortcutsState, detail: "" } }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ ok: true, outcome: "pending", granted: false }) } as Response;
    }),
  );
}

function setEmbedded(on: boolean) {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = on;
}

function needed(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "dictation",
    reason: "not_determined",
    phase: "os_dialog",
    origin: "user",
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

/** What the WS hook does when a press was refused (it reset `dictating` first). */
function refuse(reason: string) {
  act(() => {
    usePermissionsStore.getState().noteDictationRefusal({ reason, source: "refused", ts: Date.now() });
  });
}

function renderButton(dictating = false, onToggle = vi.fn()) {
  const view = render(
    <DictationButton dictating={dictating} onToggle={onToggle} startLabel="Dictate" stopLabel="Stop dictation" />,
  );
  return { ...view, onToggle };
}

beforeEach(() => {
  calls = [];
  shortcutsState = "ready";
  window.localStorage.clear();
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {}, owner: true, snapshot: null, dictationNote: null, dictationOrigin: null });
  setEmbedded(true);
  installFetch();
});
afterEach(() => {
  cleanup();
  setEmbedded(false);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("a press that did nothing, explained at the dictation button", () => {
  it("says nothing until a press in THIS window was refused", () => {
    publish("PermissionNeeded", needed());
    renderButton();
    // Another window (or a key) raised the episode: this composer never pressed.
    expect(screen.queryByTestId("dictation-permission-note")).toBeNull();
    expect(screen.queryByTestId("dictation-refused-note")).toBeNull();
  });

  it("first press on a Mac: macOS is asking, and nothing starts retroactively", () => {
    const { onToggle } = renderButton();
    publish("PermissionNeeded", needed());
    refuse("microphone_unavailable");

    const note = screen.getByTestId("dictation-permission-note");
    expect(note.getAttribute("data-phase")).toBe("os_dialog");
    expect(note.textContent).toContain("macOS is asking for access to “Microphone”");
    expect(note.textContent).not.toContain("BACKEND DETAIL");
    // The floating card has an inline surface for this feature and stays quiet.
    expect(usePermissionsStore.getState().inline).toEqual({ dictation: 1 });
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("after the grant it says 'allowed - press again' and still starts nothing", () => {
    const { onToggle } = renderButton();
    publish("PermissionNeeded", needed());
    refuse("microphone_unavailable");

    publish("PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true });

    const note = screen.getByTestId("dictation-permission-note");
    expect(note.getAttribute("data-phase")).toBe("allowed");
    expect(note.textContent).toBe("Microphone allowed. Press the microphone again to start dictating.");
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("a blocked microphone gets the full sentence and the one click to System Settings", async () => {
    renderButton();
    publish("PermissionNeeded", needed({ phase: "blocked", reason: "denied" }));
    refuse("microphone_unavailable");

    expect(screen.getByTestId("dictation-permission-note").textContent).toContain(
      "Dictation cannot work because Personal Jarvis has no access to “Microphone”.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Open System Settings" }));
    await waitFor(() =>
      expect(calls.some((call) => call.url.startsWith("/api/permissions/microphone/open-settings"))).toBe(true),
    );
  });

  it("any other refusal is one plain sentence in the person's language", () => {
    renderButton();
    refuse("no_stt");
    expect(screen.getByTestId("dictation-refused-note").textContent).toContain(
      "No speech-to-text provider is set up yet.",
    );
  });

  it("a refusal token without its own sentence reads 'did not start' instead of a bare key", () => {
    renderButton();
    refuse("something_new");
    expect(screen.getByTestId("dictation-refused-note").textContent).toContain("Dictation did not start.");
    expect(dictationRefusalKey("something_new")).toBe("permissions.inline.dictation.refused.generic");
    expect(dictationRefusalKey("no_stt")).toBe("permissions.inline.dictation.refused.no_stt");
  });

  it("a plain refusal goes away by itself after a few seconds", () => {
    vi.useFakeTimers();
    renderButton();
    refuse("already_running");
    expect(screen.getByTestId("dictation-refused-note")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(12_100);
    });
    expect(screen.queryByTestId("dictation-refused-note")).toBeNull();
  });

  it("a grant from BEFORE the refusal does not turn an unrelated refusal into 'allowed'", () => {
    renderButton();
    const now = Date.now();
    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "", { permissions: ["microphone"], feature: "dictation", granted: true }, now - 1_000);
      usePermissionsStore.getState().noteDictationRefusal({ reason: "no_stt", source: "refused", ts: now });
    });

    expect(screen.queryByTestId("dictation-permission-note")).toBeNull();
    expect(screen.getByTestId("dictation-refused-note").textContent).toContain("No speech-to-text provider is set up yet.");
  });

  it("a grant AFTER the refusal says 'allowed - press again'", () => {
    renderButton();
    const now = Date.now();
    act(() => {
      usePermissionsStore.getState().noteDictationRefusal({ reason: "microphone_unavailable", source: "refused", ts: now });
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "", { permissions: ["microphone"], feature: "dictation", granted: true }, now + 500);
    });

    expect(screen.getByTestId("dictation-permission-note").getAttribute("data-phase")).toBe("allowed");
  });

  it("with several composers mounted only the one that was pressed shows the note", () => {
    render(
      <>
        <DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate A" stopLabel="Stop A" />
        <DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate B" stopLabel="Stop B" />
      </>,
    );
    publish("PermissionNeeded", needed({ phase: "blocked", reason: "denied" }));

    // The person pressed the second composer's microphone; the window then reports the refusal.
    fireEvent.click(screen.getByRole("button", { name: "Dictate B" }));
    refuse("microphone_unavailable");

    expect(screen.getAllByTestId("dictation-permission-note")).toHaveLength(1);
    expect(usePermissionsStore.getState().inline).toEqual({ dictation: 1 });
    expect(screen.getAllByRole("button", { name: "Open System Settings" })).toHaveLength(1);
  });

  it("the close button hides it", () => {
    renderButton();
    refuse("no_stt");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("dictation-refused-note")).toBeNull();
    expect(usePermissionsStore.getState().dictationNote).toBeNull();
  });

  it("a new press supersedes the last note and still toggles", () => {
    const { onToggle } = renderButton();
    refuse("no_stt");

    fireEvent.click(screen.getByTestId("dictation-button"));

    expect(usePermissionsStore.getState().dictationNote).toBeNull();
    expect(screen.queryByTestId("dictation-refused-note")).toBeNull();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("keeps its explanation out of the way of the live recording pill", () => {
    renderButton(true);
    expect(screen.getByTestId("dictation-status")).toBeTruthy();
    expect(screen.queryByTestId("dictation-refused-note")).toBeNull();
  });
});

describe("the one-time shortcuts tip after the first dictation that really ran", () => {
  beforeEach(() => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    shortcutsState = "needs_input_monitoring";
  });

  it("appears after a dictation ended without a refusal, asks only from its button, and is dismissible", async () => {
    const { rerender } = renderButton(true);
    rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);

    const tip = await screen.findByTestId("shortcuts-tip");
    expect(tip.textContent).toContain("Dictate from any app");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Enable global shortcuts" }));
    await waitFor(() =>
      expect(calls.some((call) => call.url === "/api/permissions/input_monitoring/request?dry_run=false")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId("shortcuts-tip")).toBeNull());
  });

  it("never appears a second time", async () => {
    const first = renderButton(true);
    first.rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    await screen.findByTestId("shortcuts-tip");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("shortcuts-tip")).toBeNull();
    first.unmount();

    const second = renderButton(true);
    second.rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId("shortcuts-tip")).toBeNull();
  });

  it("does not appear when the dictation ended because it was refused", async () => {
    const { rerender } = renderButton(true);
    refuse("microphone_unavailable");
    rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId("shortcuts-tip")).toBeNull();
    expect(calls.some((call) => call.url === "/api/settings/keybinds")).toBe(false);
  });

  it("does not appear when global shortcuts already work", async () => {
    shortcutsState = "ready";
    const { rerender } = renderButton(true);
    rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    await waitFor(() => expect(calls.some((call) => call.url === "/api/settings/keybinds")).toBe(true));
    expect(screen.queryByTestId("shortcuts-tip")).toBeNull();
  });
});

describe("which way the note opens", () => {
  /** Put the button wrapper (the note's anchor) at a given horizontal spot. */
  function placeButtonAt(left: number, width = 32) {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const anchor = this.classList.contains("relative") && this.querySelector("button, [data-testid]") !== null;
      return {
        x: anchor ? left : 0,
        y: 0,
        left: anchor ? left : 0,
        right: anchor ? left + width : 0,
        top: 0,
        bottom: 0,
        width: anchor ? width : 0,
        height: 0,
        toJSON: () => ({}),
      } as DOMRect;
    });
  }

  function refusedNote() {
    renderButton();
    publish("PermissionNeeded", needed({ phase: "blocked", reason: "denied" }));
    refuse("microphone_unavailable");
    return screen.getByTestId("dictation-permission-note").parentElement as HTMLElement;
  }

  it("opens to the left beside a button at the right edge of the window", () => {
    placeButtonAt(window.innerWidth - 60);
    const box = refusedNote();
    expect(box.getAttribute("data-align")).toBe("right");
    expect(box.className).toContain("right-0");
    expect(box.className).not.toContain("left-0");
  });

  it("opens to the right from a button at the left of a composer", () => {
    placeButtonAt(24);
    const box = refusedNote();
    expect(box.getAttribute("data-align")).toBe("left");
    expect(box.className).toContain("left-0");
  });
});
