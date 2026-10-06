import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { useEventStore } from "@/store/events";

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
  SIDEBAR_RAIL_AT_WIDTH: 168,
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
vi.mock("@/components/layout/InputIsolationBanner", () => ({
  InputIsolationBanner: () => null,
}));
vi.mock("@/components/layout/VoiceWarmingBanner", () => ({
  VoiceWarmingBanner: () => null,
}));
vi.mock("@/components/voice/SubscriptionRealtimeTransportBroker", () => ({
  SubscriptionRealtimeTransportBroker: () => null,
}));
vi.mock("@/components/voice/BrowserRealtimeControl", () => ({
  BrowserRealtimeControl: () => null,
  hasEmbeddedDesktopBridge: () => false,
}));
vi.mock("@/components/ToastLayer", () => ({ ToastLayer: () => null }));
vi.mock("@/components/EditContextMenu", () => ({ EditContextMenu: () => null }));
vi.mock("@/components/JarvisDock", () => ({ JarvisDock: () => null }));
vi.mock("@/components/CliConnectPoller", () => ({ CliConnectPoller: () => null }));
vi.mock("@/components/onboarding/OnboardingGate", () => ({
  OnboardingGate: () => null,
}));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  useEventStore.setState({
    activeSection: "chats",
    solo: false,
    detachedViews: [],
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App shell around detached coding views", () => {
  it("keeps the main sidebar while Settings floats over the app", () => {
    render(<App />);
    expect(screen.getByTestId("sidebar")).toBeTruthy();

    act(() => useEventStore.setState({ activeSection: "profile" }));
    expect(screen.getByTestId("sidebar")).toBeTruthy();
    expect(screen.getByTestId("sidebar-resizer")).toBeTruthy();
  });

  it("paints the sections on the flat theme ground, with no wallpaper layer", () => {
    render(<App />);

    // Wallpapers were removed (2026-09-30): the app has one ground, the theme
    // colour, and nothing is layered behind the sections.
    expect(screen.queryByTestId("jarvis-desktop-wallpaper")).toBeNull();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
    expect(document.documentElement.classList.contains("jarvis-wallpaper")).toBe(false);
  });

  it.each(["agents", "docs", "memory"] as const)("gives the %s section the whole window", (section) => {
    useEventStore.setState({ activeSection: section });

    render(<App />);

    expect(screen.queryByTestId("sidebar")).toBeNull();
    expect(screen.queryByTestId("sidebar-resizer")).toBeNull();
    expect(screen.getByTestId("main-view")).toBeTruthy();
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
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });

  it("keeps a detached solo window free of the app navigation", () => {
    useEventStore.setState({
      activeSection: "agentic-ide",
      solo: true,
      detachedViews: ["agentic-ide"],
    });

    render(<App />);

    expect(screen.queryByTestId("sidebar")).toBeNull();
    // The caption stays: it is that window's title bar.
    expect(screen.getByTestId("topbar")).toBeTruthy();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
  });
});
