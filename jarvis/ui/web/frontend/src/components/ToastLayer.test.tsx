import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/i18n";
import { EMPTY_PROMPTS, PERMISSION_CARD_OFFSET_VAR } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";
import PermissionPromptLayer from "./permissions/PermissionPromptLayer";
import { ToastLayer } from "./ToastLayer";

const CARD_HEIGHT = 141.2;

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ toasts: [] });
  usePermissionsStore.setState({
    ...EMPTY_PROMPTS,
    snapshot: {
      platform: "darwin",
      supported: true,
      headless: false,
      app_identity: { app_name: "Personal Jarvis", bundle_id: "x", bundle_path: null, launched_as_bundle: true, stable: true },
      outside_installed_app: false,
      permissions: [],
      needed: [],
    },
    owner: true,
    inline: {},
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response),
  );
  // jsdom lays nothing out: give the permission card column a real height.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const height = this.dataset.testid === "permission-prompt-layer" ? CARD_HEIGHT : 0;
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: height, width: 320, height, toJSON: () => ({}) } as DOMRect;
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.style.removeProperty(PERMISSION_CARD_OFFSET_VAR);
});

describe("toasts under the permission card", () => {
  it("starts the toast column beneath the card (they share the top-right corner)", async () => {
    render(
      <>
        <ToastLayer />
        <PermissionPromptLayer />
      </>,
    );
    act(() => {
      useEventStore.getState().pushToast("info", "Saved");
      usePermissionsStore.getState().ingest(
        "PermissionNeeded",
        "t",
        {
          permissions: ["microphone"],
          feature: "dictation",
          reason: "denied",
          phase: "blocked",
          origin: "user",
          target: "",
          can_prompt: false,
          can_open_settings: true,
          outside_app: false,
          detail: "",
        },
        Date.now(),
      );
    });
    await screen.findByTestId("permission-prompt-card");

    // ceil(height) + an 8 px gap, published on <html> ...
    const offset = `${Math.ceil(CARD_HEIGHT) + 8}px`;
    expect(document.documentElement.style.getPropertyValue(PERMISSION_CARD_OFFSET_VAR)).toBe(offset);
    // ... and consumed by the toast column as its top margin.
    const toast = screen.getByText("Saved");
    const column = toast.closest<HTMLElement>(".fixed");
    expect(column?.style.marginTop).toBe(`var(${PERMISSION_CARD_OFFSET_VAR}, 0px)`);
  });

  it("falls back to no offset when no card is up", () => {
    render(<ToastLayer />);
    act(() => {
      useEventStore.getState().pushToast("info", "Saved");
    });

    const column = screen.getByText("Saved").closest<HTMLElement>(".fixed");
    expect(document.documentElement.style.getPropertyValue(PERMISSION_CARD_OFFSET_VAR)).toBe("");
    expect(column?.style.marginTop).toBe(`var(${PERMISSION_CARD_OFFSET_VAR}, 0px)`);
  });
});
