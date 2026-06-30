import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key (assert keys exactly).
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { JarvisAgentSection } from "./JarvisAgentSection";

const STATUS = {
  configured: true,
  enabled: true,
  binary_path: "openclaw",
  binary_detected: "/usr/bin/openclaw",
  version_pin: null,
  time_cap_min: null,
  concurrency: null,
  state_dir_root: null,
  brain_primary: "claude-api",
  provider_slug: "claude-cli",
  model_override: null,
  sub_model_override: "claude-sonnet-4-6",
  model_resolved: "claude-cli/claude-sonnet-4-6",
  mapping: [],
};

const MODELS = {
  provider: "claude-api",
  current_model: "claude-sonnet-4-6",
  models: [
    { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
  ],
  source: "static",
  fetched_at: 0,
  selects: "model",
};

function mockFetch() {
  return vi.fn().mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes("/api/jarvis-agent/status")) return { ok: true, json: async () => STATUS };
    if (u.includes("/models")) return { ok: true, json: async () => MODELS };
    if (u.includes("/api/jarvis-agent/model")) {
      return {
        ok: true,
        json: async () => ({
          ok: true,
          model: "claude-opus-4-8",
          persisted: true,
          restart_required: true,
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("JarvisAgentSection — dedicated subagent LLM dropdown", () => {
  it("opens a model dropdown for the active subagent provider", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<JarvisAgentSection />);
    const trigger = (await screen.findByLabelText(
      "apikeys_model.model_label",
    )) as HTMLElement;
    fireEvent.click(trigger);
    expect(await screen.findByText("claude-opus-4-8")).toBeTruthy();
  });

  it("saves a picked model via POST /api/jarvis-agent/model", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<JarvisAgentSection />);
    const trigger = (await screen.findByLabelText(
      "apikeys_model.model_label",
    )) as HTMLElement;
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByText("claude-opus-4-8"));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes("/api/jarvis-agent/model") &&
          (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(JSON.parse((post![1] as RequestInit).body as string).model).toBe(
        "claude-opus-4-8",
      );
    });
  });
});
