import { cleanup, render, screen } from "@testing-library/react";
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
  it("renders the desktop wallpaper behind normal app sections", () => {
    render(<App />);

    expect(screen.getByTestId("jarvis-desktop-wallpaper")).toBeTruthy();
    expect(
      screen.getByTestId("main-view").parentElement?.classList.contains("jarvis-section-stage"),
    ).toBe(true);
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
