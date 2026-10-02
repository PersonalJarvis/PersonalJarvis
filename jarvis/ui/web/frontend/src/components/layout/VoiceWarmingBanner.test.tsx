/**
 * The voice-ready banner and the sidebar header read the same microphone
 * permission (`useVoiceBlockedByPermission`), so they cannot disagree: the
 * banner never says "you can speak now" while the sidebar says "Microphone
 * blocked". macOS asking by itself is not a block, and warming is unchanged.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceWarmingBanner } from "@/components/layout/VoiceWarmingBanner";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";

const READY = "Ready – you can speak now";
const BLOCKED = "Voice is ready, but the microphone is blocked. Fix it in System Settings.";

function voice(connected: boolean, voiceReady: boolean, wsWarming = false) {
  act(() => useEventStore.setState({ connected, voiceReady, wsWarming }));
}

function microphoneEpisode(overrides: Record<string, unknown> = {}) {
  act(() => {
    usePermissionsStore.getState().ingest(
      "PermissionNeeded",
      "",
      {
        permissions: ["microphone"],
        feature: "wake_word",
        reason: "denied",
        phase: "blocked",
        origin: "background",
        target: "",
        can_prompt: false,
        can_open_settings: true,
        outside_app: false,
        detail: "",
        ...overrides,
      },
      Date.now(),
    );
  });
}

function grant() {
  act(() => {
    usePermissionsStore
      .getState()
      .ingest("PermissionResolved", "", { permissions: ["microphone"], feature: "wake_word", granted: true }, Date.now());
  });
}

/** Mount while warming, then flip to ready: the warming -> ready transition flashes the go-ahead. */
function mountAndBecomeReady() {
  voice(true, false);
  const view = render(<VoiceWarmingBanner />);
  voice(true, true);
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {} });
  useEventStore.setState({ connected: false, voiceReady: false, wsWarming: false });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("VoiceWarmingBanner", () => {
  it("warms with the starting-up title, whatever the microphone says", () => {
    microphoneEpisode();
    voice(true, false);
    render(<VoiceWarmingBanner />);

    expect(screen.getByTestId("voice-warming-banner").getAttribute("data-state")).toBe("warming");
    expect(screen.queryByText(BLOCKED)).toBeNull();
    expect(screen.queryByText(READY)).toBeNull();
  });

  it("confirms 'you can speak now' on becoming ready when the microphone is not blocked", () => {
    mountAndBecomeReady();

    const banner = screen.getByTestId("voice-warming-banner");
    expect(banner.getAttribute("data-state")).toBe("ready");
    expect(screen.getByText(READY)).toBeTruthy();
    expect(screen.queryByText(BLOCKED)).toBeNull();
  });

  it("does not claim the user can speak while macOS has the microphone blocked", () => {
    microphoneEpisode();
    mountAndBecomeReady();

    const banner = screen.getByTestId("voice-warming-banner");
    expect(banner.getAttribute("data-state")).toBe("blocked");
    expect(screen.getByText(BLOCKED)).toBeTruthy();
    expect(screen.queryByText(READY)).toBeNull();
    expect(banner.textContent).not.toMatch(/speak now/i);
  });

  it("agrees with the sidebar's hook for the not-determined episode a background wake word opens", () => {
    // Same predicate as the sidebar header: blocked + waiting on the person is blocked.
    microphoneEpisode({ reason: "not_determined", can_prompt: true });
    mountAndBecomeReady();

    expect(screen.getByTestId("voice-warming-banner").getAttribute("data-state")).toBe("blocked");
  });

  it("keeps the normal confirmation while macOS is simply asking (its own dialog is not a block)", () => {
    microphoneEpisode({ phase: "os_dialog", reason: "not_determined" });
    mountAndBecomeReady();

    expect(screen.getByTestId("voice-warming-banner").getAttribute("data-state")).toBe("ready");
    expect(screen.getByText(READY)).toBeTruthy();
  });

  it("ignores a block on some other permission", () => {
    microphoneEpisode({ permissions: ["input_monitoring"], feature: "global_shortcuts" });
    mountAndBecomeReady();

    expect(screen.getByText(READY)).toBeTruthy();
  });

  it("follows the permission while the confirmation is up: blocked, then granted", () => {
    mountAndBecomeReady();
    expect(screen.getByText(READY)).toBeTruthy();

    microphoneEpisode();
    expect(screen.getByText(BLOCKED)).toBeTruthy();
    expect(screen.queryByText(READY)).toBeNull();

    grant();
    expect(screen.getByText(READY)).toBeTruthy();
    expect(screen.queryByText(BLOCKED)).toBeNull();
  });

  it("goes away after the confirmation window in both cases", () => {
    microphoneEpisode();
    mountAndBecomeReady();
    expect(screen.getByTestId("voice-warming-banner")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(4100);
    });

    expect(screen.queryByTestId("voice-warming-banner")).toBeNull();
  });
});
