import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import { useVoiceBlockedByPermission } from "./useVoiceBlockedByPermission";

function publish(overrides: Record<string, unknown> = {}) {
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

beforeEach(() => usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {} }));
afterEach(cleanup);

describe("useVoiceBlockedByPermission", () => {
  it("is false with no episode (and always off macOS)", () => {
    const { result } = renderHook(() => useVoiceBlockedByPermission());
    expect(result.current).toBe(false);
  });

  it("is true for a dead wake word: a BACKGROUND microphone episode that waits on the person", () => {
    const { result } = renderHook(() => useVoiceBlockedByPermission());
    publish();
    expect(result.current).toBe(true);
  });

  it("is not blocked while macOS is asking by itself, or when only a restart is pending", () => {
    const { result } = renderHook(() => useVoiceBlockedByPermission());
    publish({ phase: "os_dialog", reason: "not_determined" });
    expect(result.current).toBe(false);
    publish({ feature: "voice", reason: "restart_hint" });
    expect(result.current).toBe(false);
  });

  it("ignores permissions that are not the microphone", () => {
    const { result } = renderHook(() => useVoiceBlockedByPermission());
    publish({ permissions: ["input_monitoring"], feature: "global_shortcuts" });
    expect(result.current).toBe(false);
  });

  it("clears when the grant arrives", () => {
    const { result } = renderHook(() => useVoiceBlockedByPermission());
    publish();
    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "", { permissions: ["microphone"], feature: "wake_word", granted: true }, Date.now());
    });
    expect(result.current).toBe(false);
  });
});
