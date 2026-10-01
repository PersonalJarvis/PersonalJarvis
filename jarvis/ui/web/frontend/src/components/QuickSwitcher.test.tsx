/**
 * The quick switcher end to end: type, Enter, you are there.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Radix Dialog and cmdk touch browser APIs jsdom does not ship.
class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverPolyfill }).ResizeObserver =
    ResizeObserverPolyfill;
}
if (typeof Element !== "undefined" && !("scrollIntoView" in Element.prototype)) {
  // @ts-expect-error -- jsdom shim
  Element.prototype.scrollIntoView = () => {};
}

import { QuickSwitcher } from "./QuickSwitcher";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useSettingsJump } from "@/store/settingsJump";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useIdeProjectsStore } from "@/store/ideProjects";

function renderOpen() {
  const state = { open: true };
  const onOpenChange = (next: boolean) => {
    state.open = next;
  };
  render(<QuickSwitcher open onOpenChange={onOpenChange} />);
  return state;
}

describe("QuickSwitcher", () => {
  beforeEach(() => {
    useEventStore.getState().setActiveSection("chats");
    useSettingsJump.setState({ target: null });
  });
  afterEach(cleanup);

  it("shows the field alone until something is typed", () => {
    renderOpen();
    expect(screen.queryByTestId("quick-switch-agentic-ide")).toBeNull();
    expect(document.querySelectorAll("[cmdk-item]")).toHaveLength(0);
  });

  it("lists the A-names A to Z after one letter", () => {
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "a" } });
    // Each group is alphabetical on its own; the sections group comes first.
    const sections = document.querySelector("[cmdk-group]");
    const labels = [...(sections?.querySelectorAll("[cmdk-item]") ?? [])].map(
      (row) => row.querySelector("span.min-w-0")?.textContent ?? "",
    );
    expect(labels.length).toBeGreaterThan(3);
    // Every SHOWN name starts with A — "Voice › API Keys" does not, so it is out.
    for (const label of labels) expect(label.toLowerCase().startsWith("a")).toBe(true);
    const sorted = [...labels].sort((x, y) => x.localeCompare(y, undefined, { sensitivity: "base" }));
    expect(labels).toEqual(sorted);
  });

  it("goes to the top match on Enter and closes", () => {
    const state = renderOpen();
    const input = screen.getByTestId("quick-switcher-input");
    fireEvent.change(input, { target: { value: "agentic" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    expect(state.open).toBe(false);
  });

  it("flips the front page to the face that was picked", () => {
    useHomeStore.getState().setSurface("voice");
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "chat" } });
    fireEvent.click(screen.getByTestId("quick-switch-chat"));
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(useHomeStore.getState().surface).toBe("chat");
  });

  it("hands a Settings group to the hub", () => {
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "wake word" } });
    fireEvent.click(screen.getByTestId("quick-switch-setting-wake-word"));
    expect(useEventStore.getState().activeSection).toBe("settings");
    expect(useSettingsJump.getState().target).toBe("wake-word");
  });

  it("says so when nothing matches", () => {
    renderOpen();
    fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "zzqxv" } });
    expect(screen.queryByTestId("quick-switch-settings")).toBeNull();
  });

  describe("live results", () => {
    const PANE = {
      workspace_id: "ws-2",
      workspace_name: "Blog",
      folder: "/work/blog",
      workspace_active: false,
      key: "T3",
      history_id: "h1",
      name: "codex-3",
      agent: "codex",
      display_name: "Codex",
      accepts_prompts: true,
      status: "live",
      exit_code: null,
      activity: "idle",
      activity_since: 0,
      worked: true,
      started_at: 0,
      last_output_at: 0,
      last_prompt: "speed up the mobile pages",
      last_prompt_at: 0,
      recap: "Blog speed: mobile pages twice as fast",
      has_resume: false,
      readable: true,
      account: null,
      account_label: null,
      archived: false,
    };

    beforeEach(() => {
      useEventStore.setState({
        conversations: [
          {
            kind: "voice",
            id: "c1",
            title: "Browser acceptance",
            preview: "check the browser",
            created_ms: 1,
            updated_ms: 2,
            message_count: 3,
          },
        ],
      });
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          if (String(url).includes("/api/agentic-ide/panes")) {
            return new Response(JSON.stringify({ panes: [PANE], active_id: "ws-1" }), { status: 200 });
          }
          return new Response("{}", { status: 404 });
        }),
      );
    });
    afterEach(() => vi.unstubAllGlobals());

    it("finds a chat by the first letter of its title", () => {
      renderOpen();
      expect(screen.queryByTestId("quick-switch-chat-c1")).toBeNull();
      fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "b" } });
      expect(screen.getByTestId("quick-switch-chat-c1")).toBeTruthy();
    });

    it("finds a chat by several words of its title", () => {
      renderOpen();
      fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "browser accept" } });
      expect(screen.getByTestId("quick-switch-chat-c1")).toBeTruthy();
    });

    it("finds a terminal by its title and frames it in its workspace", async () => {
      renderOpen();
      fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "mobile pages" } });
      const row = await screen.findByTestId("quick-switch-pane-ws-2-codex-3");
      fireEvent.click(row);
      expect(useEventStore.getState().activeSection).toBe("agentic-ide");
      expect(useIdeProjectsStore.getState().action).toMatchObject({
        kind: "activate-workspace",
        workspaceId: "ws-2",
      });
      expect(useIdeSidePanelStore.getState().spotlight).toEqual({ workspaceId: "ws-2", pane: "codex-3" });
    });

    it("selects the top row again when late results push in above it", async () => {
      renderOpen();
      // "blog" matches no section and no chat at first; the terminal arrives
      // with the fetch and must become the selected top row, so Enter opens it.
      fireEvent.change(screen.getByTestId("quick-switcher-input"), { target: { value: "blog speed" } });
      const row = await screen.findByTestId("quick-switch-pane-ws-2-codex-3");
      await waitFor(() => expect(row.getAttribute("data-selected")).toBe("true"));
    });

    it("opens with the text typed into the sidebar bar", () => {
      render(<QuickSwitcher open onOpenChange={() => {}} initialQuery="agen" />);
      expect((screen.getByTestId("quick-switcher-input") as HTMLInputElement).value).toBe("agen");
    });
  });
});
