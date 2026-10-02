import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Identity translator so assertions can match exact i18n keys. The rest of the
// module stays real: the inline permission notes under the switches use `fill`
// and `useUiLanguage`.
vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

// The group reads pushToast from the store; the mute-music tests read what it pushed.
const pushToast = vi.hoisted(() => vi.fn());
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) => selector({ pushToast }),
}));

// Mascot SVG is irrelevant to this group's structure — stub it out.
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div>MASCOT</div>,
}));

// The three data hooks resolve to safe, loaded defaults.
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: { style: "jarvis_bar", options: ["jarvis_bar", "mascot", "none"] },
    loading: false,
    error: null,
    saveStyle: vi.fn(),
  }),
}));
vi.mock("@/hooks/useBarPersistent", () => ({
  useBarPersistent: () => ({ enabled: true, loading: false, setEnabled: vi.fn() }),
}));
vi.mock("@/hooks/useBarFollowCursor", () => ({
  useBarFollowCursor: () => ({ enabled: true, loading: false, setEnabled: vi.fn() }),
}));
// The switch keeps its own state like the real hook, and the PUT answer is the
// test's to script (`muteSetEnabled`).
const muteSetEnabled = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useMuteMusic", async () => {
  const React = await import("react");
  return {
    useMuteMusic: () => {
      const [enabled, setEnabledState] = React.useState(false);
      return {
        enabled,
        loading: false,
        setEnabled: async (next: boolean) => {
          const answer = await muteSetEnabled(next);
          setEnabledState(next);
          return answer;
        },
      };
    },
  };
});
const setSoundEffects = vi.fn().mockResolvedValue({ ok: true, enabled: false });
vi.mock("@/hooks/useSoundEffects", () => ({
  useSoundEffects: () => ({
    enabled: true,
    loading: false,
    setEnabled: setSoundEffects,
  }),
}));

import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
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

  it("renders the overlay-style panel and all four behavior toggles", () => {
    render(<OverlayTaskbarGroup />);
    expect(screen.getByText("settings_view.overlay_style.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.bar_persistent.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.follow_cursor.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.mute_music.title")).toBeDefined();
    expect(screen.getByText("taskbar_view.sound_effects.title")).toBeDefined();
  });

  it("toggling the sound-effects switch calls the hook with the new value", () => {
    render(<OverlayTaskbarGroup />);
    // Behavior block order: bar_persistent, follow_cursor, mute_music,
    // sound_effects — so the sound-effects switch is still the last one.
    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[switches.length - 1]);
    expect(setSoundEffects).toHaveBeenCalledWith(false);
  });

  it("offers the three overlay-style options", () => {
    render(<OverlayTaskbarGroup />);
    expect(
      screen.getByText("settings_view.overlay_style.options.jarvis_bar"),
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

describe("mute music: the switch is the gesture that may make macOS ask", () => {
  beforeEach(() => {
    muteSetEnabled.mockReset();
    pushToast.mockClear();
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
  });

  function muteSwitch() {
    // Behavior block order: bar_persistent, follow_cursor, mute_music, sound_effects.
    return screen.getAllByRole("switch").find((el) => el.getAttribute("aria-label") === "taskbar_view.mute_music.title")!;
  }

  it("names the player in the inline status and keeps no refresh event", async () => {
    muteSetEnabled.mockResolvedValue({
      ok: true,
      enabled: true,
      persisted: true,
      applied_live: true,
      permission: {
        feature: "audio_ducking",
        checked: true,
        asked: true,
        note: "",
        not_running: ["Music"],
        players: [
          {
            player: "Spotify",
            target: "com.spotify.client",
            outcome: "needs_settings",
            reason: "needs_settings",
            can_open_settings: true,
            asked: true,
            outside_installed_app: false,
            detail: "ENGLISH BACKEND DETAIL",
          },
        ],
      },
    });
    const refreshes = vi.fn();
    window.addEventListener("jarvis:permissions-refresh", refreshes);

    render(<OverlayTaskbarGroup />);
    fireEvent.click(muteSwitch());

    const status = await screen.findByTestId("mute-music-status");
    // The identity translator returns the keys; the wording (and the player's
    // name inside it) is covered by MuteMusicPermissionNote.test.tsx.
    expect(status.textContent).toContain("permissions.inline.audio_ducking.player_blocked");
    expect(status.textContent).toContain("permissions.inline.audio_ducking.player_not_running");
    expect(refreshes).not.toHaveBeenCalled();
    window.removeEventListener("jarvis:permissions-refresh", refreshes);
  });

  function answerFor(outcome: string) {
    return {
      ok: true,
      enabled: true,
      persisted: true,
      applied_live: true,
      permission: {
        feature: "audio_ducking",
        checked: true,
        asked: true,
        note: "",
        not_running: [],
        players: [
          {
            player: "Spotify",
            target: "com.spotify.client",
            outcome,
            reason: outcome === "granted" ? "" : outcome,
            can_open_settings: true,
            asked: true,
            outside_installed_app: false,
            detail: "",
          },
        ],
      },
    };
  }

  it.each(["denied", "needs_settings", "unavailable"])(
    "pushes no 'is on' success toast when the player answer is %s (the line under the switch says it)",
    async (outcome) => {
      muteSetEnabled.mockResolvedValue(answerFor(outcome));
      render(<OverlayTaskbarGroup />);

      fireEvent.click(muteSwitch());

      await screen.findByTestId("mute-music-status");
      expect(pushToast).not.toHaveBeenCalled();
    },
  );

  it("still confirms with a toast when every running player allowed it", async () => {
    muteSetEnabled.mockResolvedValue(answerFor("granted"));
    render(<OverlayTaskbarGroup />);

    fireEvent.click(muteSwitch());

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("success", "taskbar_view.mute_music.enabled_toast"));
  });

  it("says macOS may be asking while the request is out, and shows nothing when switched off", async () => {
    let release: (value: unknown) => void = () => undefined;
    muteSetEnabled.mockImplementation(
      () => new Promise((resolve) => {
        release = resolve;
      }),
    );
    render(<OverlayTaskbarGroup />);
    fireEvent.click(muteSwitch());

    expect(await screen.findByTestId("mute-music-asking")).toBeTruthy();

    release({ ok: true, enabled: true, persisted: true, applied_live: true });
    await waitFor(() => expect(screen.queryByTestId("mute-music-asking")).toBeNull());
    expect(screen.queryByTestId("mute-music-status")).toBeNull();
  });
});
