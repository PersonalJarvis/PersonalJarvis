/**
 * The Keyboard shortcuts page: its four sections, and the key tester telling
 * what a pressed combination does — through the real matchers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ShortcutsView } from "./ShortcutsView";
import { useEventStore } from "@/store/events";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import {
  QUICK_SWITCH_STORAGE_KEY,
  readQuickSwitchSettings,
  useQuickSwitchSettings,
} from "@/store/quickSwitchSettings";

const KEYBINDS = {
  keybinds: {
    call: "f3+f4",
    hangup: "f5",
    dictate: "ctrl+right_alt+j",
    dictate_toggle: "ctrl+right_alt+space",
    paste_last: "ctrl+alt+v",
  },
  defaults: {},
  suggestions: [],
  restart_required: false,
};

function press(init: KeyboardEventInit) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, ...init }));
  });
}

describe("ShortcutsView", () => {
  beforeEach(() => {
    window.localStorage.removeItem(QUICK_SWITCH_STORAGE_KEY);
    useQuickSwitchSettings.setState({ ...readQuickSwitchSettings(), combo: "ctrl+space" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(KEYBINDS), { status: 200 })),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the four sections", async () => {
    render(<ShortcutsView />);
    for (const id of ["app", "calls", "dictation", "workspace"]) {
      expect(screen.getByTestId(`shortcuts-section-${id}`)).toBeTruthy();
    }
    expect(await screen.findAllByText("Ctrl")).toBeTruthy();
  });

  it("names what the quick switcher chord does", () => {
    render(<ShortcutsView />);
    press({ key: " ", code: "Space", ctrlKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toContain("Quick switcher");
  });

  it("names a voice keybind and calls a free chord free", async () => {
    render(<ShortcutsView />);
    await screen.findByTestId("shortcuts-section-dictation");
    // Let the keybinds fetch settle before pressing.
    await act(async () => {});
    press({ key: "v", code: "KeyV", ctrlKey: true, altKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/paste/i);
    press({ key: "g", code: "KeyG", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/free/i);
  });

  it("ignores plain typing in a field but still reads a chord there", () => {
    render(
      <>
        <input data-testid="field" />
        <ShortcutsView />
      </>,
    );
    const field = screen.getByTestId("field");
    const before = screen.getByTestId("shortcut-tester-meaning").textContent;
    fireEvent.keyDown(field, { key: "g", code: "KeyG" });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toBe(before);
    // The hub opens with its search box focused; a chord must still register.
    fireEvent.keyDown(field, { key: "g", code: "KeyG", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/free/i);
  });

  it("sends the dictation edit to the voice section", () => {
    render(<ShortcutsView />);
    fireEvent.click(screen.getByTestId("shortcuts-edit-dictation"));
    expect(useEventStore.getState().activeSection).toBe("voice-shortcuts");
  });
});

describe("ShortcutsView on macOS without Input Monitoring", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
    cleanup();
    vi.unstubAllGlobals();
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
  });

  it("says what global shortcuts need once, for the whole page, and not once per section", async () => {
    (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
    usePermissionsStore.setState({
      ...EMPTY_PROMPTS,
      inline: {},
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
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ...KEYBINDS, shortcuts_status: { state: "needs_input_monitoring", detail: "" } }),
          { status: 200 },
        ),
      ),
    );

    render(<ShortcutsView />);

    expect(await screen.findByTestId("shortcuts-status-note")).toBeTruthy();
    expect(screen.getAllByTestId("shortcuts-status-note")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Enable global shortcuts" })).toBeTruthy();
  });

  it("keeps the full wording on this page, and the person can still close it for good", async () => {
    (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
    usePermissionsStore.setState({
      ...EMPTY_PROMPTS,
      inline: {},
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
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ...KEYBINDS, shortcuts_status: { state: "needs_input_monitoring", detail: "" } }),
          { status: 200 },
        ),
      ),
    );

    const { unmount } = render(<ShortcutsView />);
    const note = await screen.findByTestId("shortcuts-status-note");
    expect(note.getAttribute("data-variant")).toBe("full");
    expect(note.textContent).toContain("Buttons and voice work without it");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
    unmount();

    render(<ShortcutsView />);
    await screen.findByTestId("shortcuts-view");
    expect(screen.queryByTestId("shortcuts-status-note")).toBeNull();
  });
});
