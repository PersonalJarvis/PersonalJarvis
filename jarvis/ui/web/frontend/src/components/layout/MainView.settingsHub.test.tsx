import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MainView } from "./MainView";
import { useEventStore, type SectionId } from "@/store/events";

// The hub's own tab behaviour is covered by SettingsHubView.test — here only
// the ROUTING matters: every hub id must mount the hub as a dialog over the
// last regular section, and nothing else may.
vi.mock("@/views/ChatsSurface", () => ({
  ChatsSurface: () => <div data-testid="chats-surface" />,
}));
vi.mock("@/views/SettingsHubView", () => ({
  SettingsHubView: () => <div data-testid="standalone-settings" />,
  SettingsHubDialog: ({ onClose }: { onClose: () => void }) => (
    <button type="button" data-testid="settings-hub" onClick={onClose}>close</button>
  ),
}));

beforeEach(() => {
  useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MainView — every settings-hub id mounts the hub", () => {
  it("renders only the settings hub in its own window", async () => {
    useEventStore.setState({ activeSection: "profile", solo: true, detachedViews: ["settings"] });
    render(<MainView />);
    expect(await screen.findByTestId("standalone-settings")).toBeTruthy();
    expect(screen.queryByTestId("chats-surface")).toBeNull();
    expect(screen.queryByTestId("settings-hub")).toBeNull();
  });

  it("treats every settings tab as one detached window", async () => {
    useEventStore.setState({ activeSection: "apikeys", detachedViews: ["settings"] });
    render(<MainView />);
    expect(await screen.findByTestId("detached-view-placeholder")).toBeTruthy();
    expect(screen.queryByTestId("settings-hub")).toBeNull();
  });
  it.each([
    "settings",
    "taskbar",
    "languages",
    "profile",
    "agent-instructions",
    "socials",
    "apikeys",
    "telephony",
    "telephony-setup",
    "costs",
    "feedback",
  ] as SectionId[])("routes %s to the Settings hub", async (activeSection) => {
    useEventStore.setState({ activeSection });
    render(<MainView />);

    expect(await screen.findByTestId("settings-hub")).toBeTruthy();
    // The section behind the dialog stays on screen.
    expect(screen.getByTestId("chats-surface")).toBeTruthy();
  });

  it("closes back to the section it was opened from", async () => {
    useEventStore.setState({ activeSection: "chats" });
    render(<MainView />);
    act(() => useEventStore.setState({ activeSection: "apikeys" }));
    fireEvent.click(await screen.findByTestId("settings-hub"));
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("keeps non-hub sections on their own views", async () => {
    useEventStore.setState({ activeSection: "chats" });
    render(<MainView />);

    expect(await screen.findByTestId("chats-surface")).toBeTruthy();
    expect(screen.queryByTestId("settings-hub")).toBeNull();
  });
});
