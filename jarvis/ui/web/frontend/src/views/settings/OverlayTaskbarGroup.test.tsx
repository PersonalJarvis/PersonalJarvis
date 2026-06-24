import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
const setSoundEffects = vi.fn().mockResolvedValue({ ok: true, enabled: false });
vi.mock("@/hooks/useSoundEffects", () => ({
  useSoundEffects: () => ({
    enabled: true,
    loading: false,
    setEnabled: setSoundEffects,
  }),
}));

import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";
import { NonePreview } from "@/components/overlay/OverlayStylePreviews";

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

  it("renders the overlay-style panel and all three behavior toggles", () => {
    render(<OverlayTaskbarGroup />);
    expect(screen.getByText("settings_view.overlay_style.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.bar_persistent.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.mute_music.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.sound_effects.title")).toBeDefined();
  });

  it("toggling the sound-effects switch calls the hook with the new value", () => {
    render(<OverlayTaskbarGroup />);
    // Behavior block order: bar_persistent, mute_music, sound_effects.
    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[switches.length - 1]);
    expect(setSoundEffects).toHaveBeenCalledWith(false);
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

  it("keeps the None-preview strike line inside its dashed box (no protruding stub)", () => {
    // Render the preview in isolation so the only <line> is the diagonal
    // strike (rendering the whole group also pulls in Lucide icon <line>s).
    const { container } = render(<NonePreview />);
    const lines = container.querySelectorAll("line");
    expect(lines).toHaveLength(1);
    const line = lines[0];
    // The dashed pill box spans y=11..29. Both endpoints of the diagonal
    // strike must stay within that vertical band, otherwise the line sticks
    // out above and below the pill as an unclean stub (regression: was 31/9).
    const y1 = Number(line.getAttribute("y1"));
    const y2 = Number(line.getAttribute("y2"));
    for (const y of [y1, y2]) {
      expect(y).toBeGreaterThanOrEqual(11);
      expect(y).toBeLessThanOrEqual(29);
    }
    // It must also stay symmetric about the box centre (50, 20) so it reads as
    // a clean, centred strike rather than a lopsided slash.
    expect((Number(line.getAttribute("x1")) + Number(line.getAttribute("x2"))) / 2).toBe(50);
    expect((y1 + y2) / 2).toBe(20);
  });
});
