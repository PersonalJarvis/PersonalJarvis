import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

async function openPanel(testId: string): Promise<HTMLElement> {
  fireEvent.click(screen.getByTestId(testId));
  return waitFor(() => screen.getByTestId(`${testId}-panel`));
}

describe("MusicGroup", () => {
  it("renders one row per choice with the saved value on its dropdown", () => {
    render(<MusicGroup />);
    expect(screen.getByText("settings_view.music_group_title")).toBeDefined();
    expect(screen.getByText("settings_view.music.service_section")).toBeDefined();
    expect(screen.getByText("settings_view.music.playback_section")).toBeDefined();
    expect(screen.getByTestId("music-service").textContent).toContain(
      "settings_view.music.service_labels.auto",
    );
    expect(screen.getByTestId("music-playback").textContent).toContain(
      "settings_view.music.playback_labels.background",
    );
  });

  it("lists every value and says which services are connected", async () => {
    render(<MusicGroup />);
    const services = await openPanel("music-service");
    for (const key of ["auto", "spotify", "youtube_music"]) {
      expect(services.textContent).toContain(`settings_view.music.service_labels.${key}`);
    }
    // Spotify is connected, YouTube Music is not — the option lines say so.
    expect(services.textContent).toMatch(
      /service_options\.spotify — settings_view\.music\.connected/,
    );
    expect(services.textContent).toMatch(
      /service_options\.youtube_music — settings_view\.music\.not_connected/,
    );
  });

  it("says when the background player cannot run on this host", async () => {
    render(<MusicGroup />);
    const playback = await openPanel("music-playback");
    expect(playback.textContent).toMatch(/settings_view\.music\.player_unavailable/);
  });

  it("picking an option saves that value only", async () => {
    render(<MusicGroup />);
    const services = await openPanel("music-service");
    fireEvent.click(services.querySelector('[data-value="youtube_music"]')!);
    expect(save).toHaveBeenCalledWith({ preferred_service: "youtube_music" });
    const playback = await openPanel("music-playback");
    fireEvent.click(playback.querySelector('[data-value="browser"]')!);
    expect(save).toHaveBeenCalledWith({ playback: "browser" });
  });
});
