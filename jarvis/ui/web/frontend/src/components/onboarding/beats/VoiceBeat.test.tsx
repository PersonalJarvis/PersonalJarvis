import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { BeatProps } from "../WelcomeFlow";
import { meterFill, VoiceBeat } from "./VoiceBeat";

beforeAll(async () => {
  await loadLocaleChunk("onboarding");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

interface Route {
  save?: { status: number; body: object };
  activation?: { status: number; body: object };
}

function stubFetch(route: Route = {}) {
  const calls: Array<{ url: string; method: string; body?: string }> = [];
  const reply = (status: number, body: object) =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url === "/api/settings/keybinds") return reply(200, { keybinds: { call: "f3+f4" }, defaults: {} });
      if (url === "/api/settings/wake-word" && method === "PUT") {
        const r = route.save ?? { status: 200, body: { ok: true, degraded: false, persisted: true } };
        return reply(r.status, r.body);
      }
      if (url === "/api/settings/wake-word/activation") {
        const r = route.activation ?? { status: 200, body: { ok: true, enabled: true, persisted: true } };
        return reply(r.status, r.body);
      }
      if (url === "/api/settings/wake-word/mic-level") {
        return reply(200, { max_dbfs: -18, no_device: false, too_quiet: false });
      }
      return reply(200, {});
    }),
  );
  return calls;
}

function props(over: Partial<BeatProps> = {}): BeatProps {
  return {
    onb: {
      state: {
        completed: false,
        current_step: "voice",
        skipped_steps: [],
        terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" },
        wake_word_acknowledged: false,
        legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }],
        steps: [],
      },
      loading: false,
      error: null,
      refetch: vi.fn(async () => undefined),
      saveStep: vi.fn(async () => undefined),
      acceptTerms: vi.fn(async () => undefined),
      acknowledgeWakeWord: vi.fn(async () => undefined),
      complete: vi.fn(async () => undefined),
      completeTour: vi.fn(async () => undefined),
    },
    next: vi.fn(),
    skip: vi.fn(),
    report: vi.fn(),
    results: {},
    cheer: vi.fn(),
    chosenName: null,
    setChosenName: vi.fn(),
    ...over,
  };
}

describe("meterFill", () => {
  it("maps dBFS onto 0..1 and clamps", () => {
    expect(meterFill(-60)).toBe(0);
    expect(meterFill(0)).toBe(1);
    expect(meterFill(-30)).toBeCloseTo(0.5);
    expect(meterFill(-90)).toBe(0);
    expect(meterFill(Number.NaN)).toBe(0);
  });
});

describe("VoiceBeat", () => {
  it("names the assistant after the wake word while typing", async () => {
    stubFetch();
    render(<VoiceBeat {...props()} />);
    fireEvent.change(screen.getByTestId("wake-word-input"), { target: { value: "Nova" } });
    expect(screen.getByTestId("wake-derived-name").textContent).toContain("Nova");
  });

  it("saves the word only after the responsibility is ticked", async () => {
    const calls = stubFetch();
    const p = props();
    render(<VoiceBeat {...p} />);
    fireEvent.change(screen.getByTestId("wake-word-input"), { target: { value: "Nova" } });
    const save = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("wake-ack"));
    await act(async () => {
      fireEvent.click(save);
    });
    expect(p.onb.acknowledgeWakeWord).toHaveBeenCalled();
    const put = calls.find((c) => c.url === "/api/settings/wake-word" && c.method === "PUT");
    expect(JSON.parse(put!.body!).phrase).toBe("Hey Nova");
    expect(p.setChosenName).toHaveBeenCalledWith("Nova");
    expect(p.report).toHaveBeenCalledWith({ summary: "Hey Nova", gap: null });
    expect(p.next).toHaveBeenCalled();
  });

  it("does not pretend a word nothing can hear is working", async () => {
    stubFetch({ save: { status: 200, body: { ok: true, degraded: true, persisted: true } } });
    const p = props();
    render(<VoiceBeat {...p} />);
    fireEvent.change(screen.getByTestId("wake-word-input"), { target: { value: "Nova" } });
    fireEvent.click(screen.getByTestId("wake-ack"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("onboarding-primary"));
    });
    expect(screen.getByTestId("wake-degraded")).toBeDefined();
    expect(p.next).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByTestId("wake-continue-anyway"));
    });
    expect(p.next).toHaveBeenCalled();
    const report = (p.report as ReturnType<typeof vi.fn>).mock.calls.at(-1)![0];
    expect(report.gap).toContain("Hey Nova");
  });

  it("always leaves a way on after a failed call (BUG-209)", async () => {
    stubFetch({ save: { status: 500, body: { detail: "config file is locked" } } });
    const p = props();
    render(<VoiceBeat {...p} />);
    fireEvent.change(screen.getByTestId("wake-word-input"), { target: { value: "Nova" } });
    fireEvent.click(screen.getByTestId("wake-ack"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("onboarding-primary"));
    });
    expect(screen.getByText("config file is locked")).toBeDefined();
    fireEvent.click(screen.getByTestId("wake-skip-after-error"));
    expect(p.skip).toHaveBeenCalled();
  });

  it("reports a switch that did not persist", async () => {
    stubFetch({ activation: { status: 200, body: { ok: true, enabled: false, persisted: false } } });
    const p = props();
    render(<VoiceBeat {...p} />);
    fireEvent.click(screen.getByTestId("wake-mode-shortcut"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("onboarding-primary"));
    });
    const report = (p.report as ReturnType<typeof vi.fn>).mock.calls.at(-1)![0];
    expect(report.gap).toMatch(/gone after the restart/);
    expect(p.setChosenName).toHaveBeenCalledWith(null);
    expect(p.next).toHaveBeenCalled();
  });

  it("shows the Call keys on the shortcut choice", async () => {
    stubFetch();
    render(<VoiceBeat {...props()} />);
    await waitFor(() => expect(screen.getAllByText("f3").length).toBeGreaterThan(0));
  });

  it("answers the microphone test in place", async () => {
    stubFetch();
    const p = props();
    render(<VoiceBeat {...p} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("wake-mic-test"));
    });
    await waitFor(() => expect(screen.getByText(/came through clearly/)).toBeDefined());
    expect(p.cheer).toHaveBeenCalled();
  });
});
