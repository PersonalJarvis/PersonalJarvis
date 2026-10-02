import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MuteMusicPermission, MuteMusicPlayerPermission } from "@/hooks/useMuteMusic";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import { MuteMusicPermissionNote, muteMusicLines } from "./MuteMusicPermissionNote";

function player(overrides: Partial<MuteMusicPlayerPermission> = {}): MuteMusicPlayerPermission {
  return {
    player: "Spotify",
    target: "com.spotify.client",
    outcome: "granted",
    reason: "",
    can_open_settings: true,
    asked: true,
    outside_installed_app: false,
    detail: "ENGLISH BACKEND DETAIL",
    ...overrides,
  };
}

function report(overrides: Partial<MuteMusicPermission> = {}): MuteMusicPermission {
  return {
    feature: "audio_ducking",
    checked: true,
    asked: true,
    note: "",
    not_running: [],
    players: [player()],
    ...overrides,
  };
}

function setEmbedded(on: boolean) {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = on;
}

beforeEach(() => {
  usePermissionsStore.setState({
    ...EMPTY_PROMPTS,
    inline: {},
    dictationNote: null,
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
  setEmbedded(true);
});
afterEach(() => {
  cleanup();
  setEmbedded(false);
  vi.unstubAllGlobals();
});

describe("muteMusicLines", () => {
  const base = "permissions.inline.audio_ducking";

  it("has no lines without an answer (switch off, Windows, an older backend)", () => {
    expect(muteMusicLines(null, false)).toEqual([]);
    expect(muteMusicLines(undefined, false)).toEqual([]);
  });

  it("says a player must be running when none was: nothing was checked", () => {
    expect(muteMusicLines(report({ checked: false, players: [] }), false)).toEqual([
      { key: `${base}.none_running`, player: "", tone: "info", openSettings: false },
    ]);
  });

  it("names each player by its outcome and the ones that were not open", () => {
    const lines = muteMusicLines(
      report({
        not_running: ["Music"],
        players: [
          player({ player: "Spotify", outcome: "denied", reason: "denied" }),
          player({ player: "Podcasts", outcome: "pending", reason: "not_determined" }),
          player({ player: "Tidal", outcome: "unavailable", reason: "unavailable" }),
        ],
      }),
      false,
    );
    expect(lines.map((line) => [line.key.split(".").pop(), line.player, line.openSettings])).toEqual([
      ["player_blocked", "Spotify", true],
      ["player_pending", "Podcasts", false],
      ["player_unavailable", "Tidal", false],
      ["player_not_running", "Music", false],
    ]);
  });

  it("turns a pending player into an allowed one once the grant was reported", () => {
    const lines = muteMusicLines(report({ players: [player({ outcome: "pending" })] }), true);
    expect(lines[0].key).toBe(`${base}.player_granted`);
  });
});

describe("MuteMusicPermissionNote", () => {
  it("is silent when the switch is off, even with a stale answer", () => {
    render(<MuteMusicPermissionNote active={false} permission={report()} asking={false} since={0} />);
    expect(screen.queryByTestId("mute-music-status")).toBeNull();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("says macOS may be asking while the switch-on request is out", () => {
    render(<MuteMusicPermissionNote active permission={null} asking since={0} />);
    expect(screen.getByTestId("mute-music-asking").textContent).toContain(
      "If Music or Spotify is open, macOS may ask whether Personal Jarvis may control it.",
    );
  });

  it("names the player and says it is checked while the player runs", () => {
    render(
      <MuteMusicPermissionNote
        active
        permission={report({ not_running: ["Music"], players: [player()] })}
        asking={false}
        since={0}
      />,
    );
    const status = screen.getByTestId("mute-music-status").textContent ?? "";
    expect(status).toContain("Personal Jarvis may lower Spotify.");
    expect(status).toContain("Music is not running. Access is checked while it runs.");
    expect(status).not.toContain("BACKEND DETAIL");
  });

  it("offers the Automation pane for a blocked player, and registers so the card stays quiet", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MuteMusicPermissionNote
        active
        permission={report({ players: [player({ outcome: "needs_settings", reason: "needs_settings" })] })}
        asking={false}
        since={0}
      />,
    );
    expect(screen.getByTestId("mute-music-status").textContent).toContain(
      "may not control Spotify, so it will not be lowered while you dictate",
    );
    expect(usePermissionsStore.getState().inline).toEqual({ audio_ducking: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Open System Settings" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(
      "/api/permissions/automation/open-settings?dry_run=false",
    );
  });

  it("offers no host-only button to a remote browser", () => {
    setEmbedded(false);
    render(
      <MuteMusicPermissionNote
        active
        permission={report({ players: [player({ outcome: "denied", reason: "denied" })] })}
        asking={false}
        since={0}
      />,
    );
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows nothing off macOS", () => {
    usePermissionsStore.setState({ snapshot: { ...usePermissionsStore.getState().snapshot!, platform: "win32" } });
    render(<MuteMusicPermissionNote active permission={report()} asking={false} since={0} />);
    expect(screen.queryByTestId("mute-music-status")).toBeNull();
  });
});
