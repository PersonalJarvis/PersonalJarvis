import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JarvisBar } from "@/components/home/JarvisBar";
import type { WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { DICTATION_SETTINGS_EVENT } from "@/hooks/usePromptMode";
import { useEventStore } from "@/store/events";

/**
 * The Prompt Mode pill on the Jarvis bar (maintainer, 2026-08-27): a small
 * indicator that is lit while every dictation comes out as a finished prompt,
 * and flips the switch on one click — without starting a conversation, which
 * is what a click on the card itself does.
 */

const toggleCall = vi.fn();

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/components/home/StageWaveform", () => ({
  StageWaveform: () => <div data-testid="stage-waveform" />,
}));
vi.mock("@/components/agentic/useVoiceCall", () => ({
  useVoiceCall: () => ({ active: false, busy: false, connecting: false, toggleCall }),
}));
vi.mock("@/hooks/useVoiceReadiness", () => ({
  useVoiceReadiness: () => ({
    connected: true,
    warming: false,
    ready: true,
    voiceWarming: false,
    bootWarming: false,
  }),
}));
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({
  useVoiceEngineDisplay: () => ({ providerLabel: "Groq", model: "fast-model" }),
}));

type FetchCall = { url: string; init?: RequestInit };

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** A backend whose `[dictation]` block starts with Prompt Mode `initial`. */
function stubBackend(initial: boolean | "missing" | "down") {
  const calls: FetchCall[] = [];
  let promptMode = initial === true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (initial === "down") return jsonResponse({ detail: "no backend" }, 503);
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { prompt_mode?: boolean };
        if (typeof body.prompt_mode === "boolean") promptMode = body.prompt_mode;
        return jsonResponse({ settings: { prompt_mode: promptMode, polish: true } });
      }
      if (initial === "missing") return jsonResponse({ settings: { polish: true } });
      return jsonResponse({ settings: { prompt_mode: promptMode, polish: true } });
    }),
  );
  return calls;
}

function renderBar() {
  return render(<JarvisBar phase={"idle" as unknown as WaveformPhase} hint="hint" />);
}

beforeEach(() => {
  toggleCall.mockReset();
  useEventStore.setState({ toasts: [] } as never);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("JarvisBar Prompt Mode pill", () => {
  it("shows the pill unlit while Prompt Mode is off", async () => {
    stubBackend(false);
    renderBar();
    const pill = await screen.findByTestId("jarvis-bar-prompt-mode");
    expect(pill.getAttribute("aria-pressed")).toBe("false");
    expect(pill.getAttribute("data-on")).toBeNull();
    expect(pill.getAttribute("title")).toBe("home.prompt_mode_off");
  });

  it("shows the pill lit while Prompt Mode is on", async () => {
    stubBackend(true);
    renderBar();
    const pill = await screen.findByTestId("jarvis-bar-prompt-mode");
    expect(pill.getAttribute("aria-pressed")).toBe("true");
    expect(pill.getAttribute("data-on")).toBe("true");
    expect(pill.getAttribute("title")).toBe("home.prompt_mode_on");
  });

  it("one click flips the switch through the settings route and never starts a call", async () => {
    const calls = stubBackend(false);
    renderBar();
    const pill = await screen.findByTestId("jarvis-bar-prompt-mode");

    await act(async () => {
      fireEvent.click(pill);
    });

    await waitFor(() => expect(pill.getAttribute("aria-pressed")).toBe("true"));
    const put = calls.find((c) => c.init?.method === "PUT");
    expect(put?.url).toBe("/api/dictation/settings");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ prompt_mode: true, persist: true });
    expect(toggleCall).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(pill);
    });
    await waitFor(() => expect(pill.getAttribute("aria-pressed")).toBe("false"));
    expect(toggleCall).not.toHaveBeenCalled();
  });

  it("renders no pill when the backend does not answer or does not know the key", async () => {
    stubBackend("down");
    renderBar();
    await screen.findByTestId("jarvis-bar-engine");
    await act(async () => {});
    expect(screen.queryByTestId("jarvis-bar-prompt-mode")).toBeNull();
    cleanup();

    stubBackend("missing");
    renderBar();
    await screen.findByTestId("jarvis-bar-engine");
    await act(async () => {});
    expect(screen.queryByTestId("jarvis-bar-prompt-mode")).toBeNull();
  });

  it("follows a flip made on the settings screen", async () => {
    stubBackend(false);
    renderBar();
    const pill = await screen.findByTestId("jarvis-bar-prompt-mode");
    expect(pill.getAttribute("aria-pressed")).toBe("false");

    act(() => {
      window.dispatchEvent(
        new CustomEvent(DICTATION_SETTINGS_EVENT, {
          detail: { settings: { prompt_mode: true } },
        }),
      );
    });
    await waitFor(() => expect(pill.getAttribute("aria-pressed")).toBe("true"));
  });

  it("reports a failed save as a toast and keeps the old state", async () => {
    const calls = stubBackend(false);
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      if (init?.method === "PUT") {
        return jsonResponse({ detail: "Dictation settings could not be saved." }, 500);
      }
      return jsonResponse({ settings: { prompt_mode: false } });
    });
    renderBar();
    const pill = await screen.findByTestId("jarvis-bar-prompt-mode");

    await act(async () => {
      fireEvent.click(pill);
    });

    await waitFor(() => expect(useEventStore.getState().toasts.length).toBe(1));
    expect(useEventStore.getState().toasts[0]?.message).toBe(
      "Dictation settings could not be saved.",
    );
    expect(pill.getAttribute("aria-pressed")).toBe("false");
  });
});
