import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so assertions can match exact i18n keys.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

const save = vi.fn().mockResolvedValue({
  ok: true,
  preferred_service: "youtube_music",
  playback: "background",
  persisted: true,
});
vi.mock("@/hooks/useMusicSettings", () => ({
  useMusicSettings: () => ({
    settings: {
      preferred_service: "auto",
      playback: "background",
      service_options: ["auto", "spotify", "youtube_music"],
      playback_options: ["background", "browser"],
      connected: ["spotify"],
      background_player_available: false,
    },
    loading: false,
    error: null,
    refetch: vi.fn(),
    save,
  }),
}));

import { MusicGroup } from "@/views/settings/MusicGroup";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MusicGroup", () => {
  it("renders both sections with one row per value", () => {
    render(<MusicGroup />);
    expect(screen.getByText("settings_view.music_group_title")).toBeDefined();
    expect(screen.getByText("settings_view.music.service_section")).toBeDefined();
    expect(screen.getByText("settings_view.music.playback_section")).toBeDefined();
    for (const key of ["auto", "spotify", "youtube_music"]) {
      expect(screen.getByText(`settings_view.music.service_labels.${key}`)).toBeDefined();
    }
    for (const key of ["background", "browser"]) {
      expect(screen.getByText(`settings_view.music.playback_labels.${key}`)).toBeDefined();
    }
  });

  it("marks the active rows and says which services are connected", () => {
    render(<MusicGroup />);
    const pressed = screen
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed).toHaveLength(2); // one per section
    // Spotify is connected, YouTube Music is not — the row descriptions say so.
    expect(
      screen.getByText(/service_options\.spotify — settings_view\.music\.connected/),
    ).toBeDefined();
    expect(
      screen.getByText(/service_options\.youtube_music — settings_view\.music\.not_connected/),
    ).toBeDefined();
    // The background player cannot run in this fake host — the row says so.
    expect(screen.getByText(/settings_view\.music\.player_unavailable/)).toBeDefined();
  });

  it("clicking a row saves that value only", () => {
    render(<MusicGroup />);
    fireEvent.click(screen.getByText("settings_view.music.service_labels.youtube_music"));
    expect(save).toHaveBeenCalledWith({ preferred_service: "youtube_music" });
    fireEvent.click(screen.getByText("settings_view.music.playback_labels.browser"));
    expect(save).toHaveBeenCalledWith({ playback: "browser" });
  });
});
