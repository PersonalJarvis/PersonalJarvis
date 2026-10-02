import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import { useInlinePermission } from "./useInlinePermission";

function needed(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "wake_word",
    reason: "denied",
    phase: "blocked",
    origin: "user",
    target: "",
    can_prompt: false,
    can_open_settings: true,
    outside_app: false,
    detail: "",
    ...overrides,
  };
}

beforeEach(() => {
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {}, owner: true });
});
afterEach(cleanup);

describe("useInlinePermission", () => {
  it("registers its feature while mounted, so the floating card does not repeat it", () => {
    const { unmount } = renderHook(() => useInlinePermission("wake_word"));
    expect(usePermissionsStore.getState().inline).toEqual({ wake_word: 1 });

    unmount();

    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("is ref-counted across surfaces of the same feature", () => {
    const first = renderHook(() => useInlinePermission("wake_word"));
    const second = renderHook(() => useInlinePermission("wake_word"));
    expect(usePermissionsStore.getState().inline).toEqual({ wake_word: 2 });

    first.unmount();
    expect(usePermissionsStore.getState().inline).toEqual({ wake_word: 1 });
    second.unmount();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("reads without registering when disabled", () => {
    renderHook(() => useInlinePermission("wake_word", false));

    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("hands the surface the episode of its own feature, whatever its phase or origin", () => {
    const { result } = renderHook(() => useInlinePermission("wake_word"));
    expect(result.current.episode).toBeNull();

    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", needed({ phase: "os_dialog", origin: "background", reason: "not_determined" }), 1_000);
      usePermissionsStore.getState().ingest("PermissionNeeded", "t2", needed({ feature: "voice" }), 1_000);
    });

    expect(result.current.episode).toMatchObject({ feature: "wake_word", phase: "os_dialog", origin: "background" });
  });

  it("reports the grant of its feature, for 'allowed - press again' notes", () => {
    const { result } = renderHook(() => useInlinePermission("wake_word"));
    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", needed(), 1_000);
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "t", { permissions: ["microphone"], feature: "wake_word", granted: true }, 2_000);
    });

    expect(result.current.episode).toBeNull();
    expect(result.current.resolved).toMatchObject({ feature: "wake_word", granted: true });
  });

  it("dismiss() is episode-scoped Not now", () => {
    const { result } = renderHook(() => useInlinePermission("wake_word"));
    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", needed(), 1_000);
    });

    act(() => result.current.dismiss());

    expect(result.current.episode?.dismissed).toBe(true);
  });
});
