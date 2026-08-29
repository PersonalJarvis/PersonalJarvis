import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { useEventStore } from "@/store/events";
import { useWallpaperStore } from "@/store/wallpaper";

vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: () => undefined }));
vi.mock("@/hooks/useBrainStatus", () => ({ useBrainStatus: () => undefined }));
vi.mock("@/hooks/useVoiceStatus", () => ({ useVoiceStatus: () => undefined }));
vi.mock("@/hooks/useAssistantNameSeed", () => ({
  useAssistantNameSeed: () => undefined,
}));
vi.mock("@/hooks/useCodingMode", () => ({ useCodingMode: () => undefined }));
vi.mock("@/hooks/useResizablePane", () => ({
  useResizablePane: () => ({
    size: 280,
    isResizing: false,
    startResize: vi.fn(),
    reset: vi.fn(),
    nudge: vi.fn(),
  }),
}));
vi.mock("@/lib/dictationTarget", () => ({
  installDictationFocusTracker: () => vi.fn(),
}));

vi.mock("@/components/layout/Sidebar", () => ({
  SIDEBAR_DEFAULT_WIDTH: 280,
  SIDEBAR_RAIL_WIDTH: 56,
  // App.tsx remembers the sidebar width under this key, so the mock has to
  // carry it too or the shell throws before it renders anything.
  SIDEBAR_WIDTH_STORAGE_KEY: "jarvis.sidebar.width.v3",
  Sidebar: () => <aside data-testid="sidebar" />,
}));
vi.mock("@/components/layout/PaneResizer", () => ({
  PaneResizer: () => <div data-testid="sidebar-resizer" />,
}));
vi.mock("@/components/layout/TopBar", () => ({
  TopBar: () => <div data-testid="topbar" />,
}));
vi.mock("@/components/layout/MainView", () => ({
  MainView: () => <div data-testid="main-view" />,
}));
vi.mock("@/components/layout/PermissionsAlertBanner", () => ({
  PermissionsAlertBanner: () => null,
}));
vi.mock("@/components/layout/InputIsolationBanner", () => ({
  InputIsolationBanner: () => null,
}));
vi.mock("@/components/layout/VoiceWarmingBanner", () => ({
  VoiceWarmingBanner: () => null,
}));
vi.mock("@/components/voice/SubscriptionRealtimeTransportBroker", () => ({
  SubscriptionRealtimeTransportBroker: () => null,
}));
vi.mock("@/components/ToastLayer", () => ({ ToastLayer: () => null }));
vi.mock("@/components/EditContextMenu", () => ({ EditContextMenu: () => null }));
vi.mock("@/components/JarvisDock", () => ({ JarvisDock: () => null }));
vi.mock("@/components/CliConnectPoller", () => ({ CliConnectPoller: () => null }));
vi.mock("@/components/onboarding/OnboardingGate", () => ({
  OnboardingGate: () => null,
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  useEventStore.setState({
    activeSection: "chats",
    solo: false,
    detachedViews: [],
  });
  // The ground is a flat colour by default (2026-08-23); these tests are
  // about the wallpaper layer, so they switch it on. The solid default has
  // its own test below.
  useWallpaperStore.setState({ mascotOn: true, background: "wallpaper" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App shell around detached coding views", () => {
  it("renders the desktop wallpaper behind normal app sections", () => {
    render(<App />);

    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(screen.getByTestId("jarvis-desktop-wallpaper-mascot")).toBeTruthy();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });

  it("hides the live mascot when the wallpaper layer is switched off", () => {
    useWallpaperStore.setState({ mascotOn: false });
    render(<App />);

    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(screen.queryByTestId("jarvis-desktop-wallpaper-mascot")).toBeNull();
  });

  it("paints no wallpaper layer at all on the solid ground (the default)", () => {
    useWallpaperStore.setState({ background: "solid" });
    render(<App />);

    expect(screen.queryByTestId("jarvis-desktop-wallpaper")).toBeNull();
    expect(screen.queryByTestId("jarvis-desktop-wallpaper-mascot")).toBeNull();
    // The stage class stays: the solid-ground overrides in index.css key on
    // the root class, not on the stage being absent.
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });

  it("hides the live mascot on every section except Chats", () => {
    useEventStore.setState({ activeSection: "tasks" });
    render(<App />);

    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(screen.queryByTestId("jarvis-desktop-wallpaper-mascot")).toBeNull();
  });

  it("keeps the sidebar reachable in the main window", () => {
    useEventStore.setState({
      activeSection: "agentic-ide",
      detachedViews: ["agentic-ide"],
    });

    render(<App />);

    expect(screen.getByTestId("sidebar")).toBeTruthy();
    expect(screen.getByTestId("sidebar-resizer")).toBeTruthy();
  });

  it("keeps the navigation rail visible in an attached coding workspace", () => {
    useEventStore.setState({
      activeSection: "agentic-ide",
      detachedViews: [],
    });

    render(<App />);

    expect(screen.getByTestId("sidebar")).toBeTruthy();
    expect(screen.getByTestId("sidebar-resizer")).toBeTruthy();
    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });

  it("keeps a detached solo window chrome-free", () => {
    useEventStore.setState({
      activeSection: "agentic-ide",
      solo: true,
      detachedViews: ["agentic-ide"],
    });

    render(<App />);

    expect(screen.queryByTestId("sidebar")).toBeNull();
    expect(screen.queryByTestId("topbar")).toBeNull();
    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });
});
