/**
 * Sidebar API-Keys alert dot.
 *
 * The API-Keys tab dots already say "this section is broken" once you are on the
 * page. The sidebar drills that one level up: a red dot on the "API Keys" nav row
 * makes a set-up-but-failing provider visible from anywhere in the app. Only a
 * hard "error" lights it — the amber "needs_setup" state stays off the bar so a
 * fresh install isn't permanently flagged.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { SectionHealth } from "@/hooks/useProviders";

let mockHealth: Record<string, SectionHealth> = {};

vi.mock("@/hooks/useVoiceReadiness", () => ({
  useVoiceReadiness: () => ({
    connected: true,
    voiceWarming: false,
    bootWarming: false,
    warming: false,
  }),
}));

vi.mock("@/hooks/useProviders", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useProviders")>();
  return {
    ...actual,
    useSectionHealth: () => ({ health: mockHealth, reload: vi.fn() }),
  };
});

import { Sidebar } from "@/components/layout/Sidebar";
import { useEventStore } from "@/store/events";

beforeEach(() => {
  mockHealth = {};
  useEventStore.setState({ activeSection: "chats" });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Sidebar — API-Keys alert dot", () => {
  it("shows a red alert dot on API Keys when a section reports error", () => {
    mockHealth = {
      brain: { status: "error", reason: "rate_limited", detail: "OpenRouter: rate limited" },
      tts: { status: "ok", reason: "ok", detail: "" },
    };
    render(<Sidebar />);
    const dot = screen.getByTestId("nav-alert-apikeys");
    expect(dot).toBeTruthy();
    expect(dot.className).toMatch(/bg-destructive/);
  });

  it("stays calm (no dot) when nothing is broken", () => {
    mockHealth = {
      brain: { status: "needs_setup", reason: "not_configured", detail: "" },
      tts: { status: "ok", reason: "ok", detail: "" },
      stt: { status: "unknown", reason: "unknown", detail: "" },
    };
    render(<Sidebar />);
    expect(screen.queryByTestId("nav-alert-apikeys")).toBeNull();
  });

  it("stays calm when health hasn't loaded yet (empty map)", () => {
    mockHealth = {};
    render(<Sidebar />);
    expect(screen.queryByTestId("nav-alert-apikeys")).toBeNull();
  });
});
