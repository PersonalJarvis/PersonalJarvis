import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so assertions can match exact i18n keys.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

// The group reads pushToast from the store; we never trigger it here.
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

// Mascot SVG is irrelevant to this group's structure — stub it out.
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div>MASCOT</div>,
}));

// The three data hooks resolve to safe, loaded defaults.
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: { style: "whisper_bar", options: ["whisper_bar", "mascot", "none"] },
    loading: false,
    error: null,
    saveStyle: vi.fn(),
  }),
}));
vi.mock("@/hooks/useBarPersistent", () => ({
  useBarPersistent: () => ({ enabled: true, loading: false, setEnabled: vi.fn() }),
}));
vi.mock("@/hooks/useMuteMusic", () => ({
  useMuteMusic: () => ({ enabled: false, loading: false, setEnabled: vi.fn() }),
}));

import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OverlayTaskbarGroup", () => {
  it("renders the group heading and both sub-headings", () => {
    render(<OverlayTaskbarGroup />);
    expect(
      screen.getByText("settings_view.overlay_taskbar_group_title"),
    ).toBeDefined();
    expect(screen.getByText("taskbar_view.appearance_title")).toBeDefined();
    expect(screen.getByText("taskbar_view.behavior_title")).toBeDefined();
  });

  it("renders the overlay-style panel and both dictation toggles", () => {
    render(<OverlayTaskbarGroup />);
    expect(screen.getByText("settings_view.overlay_style.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.bar_persistent.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.mute_music.title")).toBeDefined();
  });

  it("offers the three overlay-style options", () => {
    render(<OverlayTaskbarGroup />);
    expect(
      screen.getByText("settings_view.overlay_style.options.whisper_bar"),
    ).toBeDefined();
    expect(
      screen.getByText("settings_view.overlay_style.options.mascot"),
    ).toBeDefined();
    expect(
      screen.getByText("settings_view.overlay_style.options.none"),
    ).toBeDefined();
  });
});
