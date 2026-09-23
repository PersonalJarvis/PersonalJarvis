/** GPT-Live computer control shares the selected thinking model and its credential. */
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithQueryClient as render } from "@/test/queryRender";
import { ApiKeysView } from "./ApiKeysView";

vi.mock("@/hooks/useProviders", () => ({
  sectionHealthForSubject: () => undefined,
  useProviders: () => ({ providers: [], loading: false, error: null, refetch: vi.fn(), setActiveOptimistic: vi.fn() }),
  useSectionHealth: () => ({ health: {} }),
}));
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({ mode: "pipeline", realtimeAvailable: true, statusKnown: true,
    sessionActive: false, activeSessionMode: null, setMode: vi.fn(), isLoading: false }),
}));

let keyReady = true;
const calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  keyReady = true;
  calls.length = 0;
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const profile = { model: "voice-model", voice: "verse", backend_model: "thinking-small",
      reasoning_effort: "", web_search: false, instructions: "", backend_instructions: "", configured: true };
    const body = url === "/api/live/profile"
      ? { profile, key_ready: keyReady, active: true, agent_configured: true }
      : { models: [{ id: "thinking-small", label: "Small" }, { id: "thinking-large", label: "Large" }],
        voices: ["verse", "alloy"], efforts: ["", "high"] };
    return { ok: true, json: async () => body } as Response;
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openLive() {
  render(<ApiKeysView />);
  fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));
  return screen.findByRole("combobox", { name: "Thinking model" });
}

describe("shared computer-control thinking model", () => {
  it("shows the shared model instead of a separate Computer-Use provider tab", async () => {
    await openLive();
    expect(screen.queryByRole("tab", { name: /tool model/i })).toBeNull();
    expect(screen.getByText(/Computer control uses this same thinking model/)).toBeTruthy();
  });

  it("persists model changes through the live profile without switching the Brain provider", async () => {
    const model = await openLive();
    fireEvent.change(model, { target: { value: "thinking-large" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and use GPT-Live" }));
    await waitFor(() => expect(calls.some((call) => call.url === "/api/live/profile"
      && call.method === "PUT" && (call.body as { backend_model: string }).backend_model === "thinking-large")).toBe(true));
    expect(calls.some((call) => call.url === "/api/brain/switch" || call.url === "/api/computer-use/switch")).toBe(false);
  });

  it("offers the backend model catalogue on the shared model input", async () => {
    const model = await openLive();
    expect(model.getAttribute("list")).toBe("live-thinking-models");
    await waitFor(() => expect(Array.from(document.querySelectorAll("#live-thinking-models option"))
      .map((option) => option.getAttribute("value"))).toEqual(["thinking-small", "thinking-large"]));
  });

  it("requires the voice credential before saving a computer-control model", async () => {
    keyReady = false;
    await openLive();
    expect(screen.getByRole("button", { name: "Save and use GPT-Live" })).toHaveProperty("disabled", true);
    expect(screen.getByText(/Connect your OpenAI key below/)).toBeTruthy();
  });

  it("saves voice and effort selected through the themed dropdowns", async () => {
    await openLive();
    fireEvent.click(screen.getByRole("combobox", { name: "Voice" }));
    fireEvent.click(screen.getByRole("option", { name: "alloy" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Reasoning effort" }));
    fireEvent.click(screen.getByRole("option", { name: "high" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and use GPT-Live" }));
    await waitFor(() => expect(calls.find((call) => call.method === "PUT")?.body)
      .toMatchObject({ voice: "alloy", reasoning_effort: "high" }));
  });
});
