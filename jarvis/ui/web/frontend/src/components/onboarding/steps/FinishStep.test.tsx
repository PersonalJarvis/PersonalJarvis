import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { FinishStep } from "./FinishStep";

// Default stub: capability probe answers "unsupported" so the tests exercise
// the step without the autostart toggle.
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ enabled: false, supported: false }),
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const STEPS = ["welcome", "language", "api-keys", "permissions", "wake-word", "finish"];

function props(overrides: Record<string, unknown> = {}) {
  return {
    onb: {
      state: { skipped_steps: [], steps: STEPS },
      complete: vi.fn().mockResolvedValue(undefined),
    } as never,
    goNext: vi.fn(),
    goBack: vi.fn(),
    skip: vi.fn(),
    isFirst: false,
    isLast: true,
    setSummary: vi.fn(),
    setGap: vi.fn(),
    gaps: {},
    summaries: { language: "Deutsch · Auto", "api-keys": "OpenRouter", "wake-word": "Hey Nova" },
    ...overrides,
  };
}

it("awaits complete() on the start CTA", async () => {
  const p = props();
  render(<FinishStep {...p} />);
  fireEvent.click(screen.getByTestId("onboarding-start"));
  await waitFor(() =>
    expect((p.onb as never as { complete: ReturnType<typeof vi.fn> }).complete).toHaveBeenCalled(),
  );
});

it("a failed completion shows the error and re-enables the button", async () => {
  const p = props({
    onb: {
      state: { skipped_steps: [], steps: STEPS },
      complete: vi.fn().mockRejectedValue(new Error("HTTP 500")),
    } as never,
  });
  render(<FinishStep {...p} />);
  fireEvent.click(screen.getByTestId("onboarding-start"));
  await waitFor(() => expect(screen.getByText("onboarding.finish.start_failed")).toBeDefined());
  expect((screen.getByTestId("onboarding-start") as HTMLButtonElement).disabled).toBe(false);
});

it("reads back the register summaries and marks skipped steps", () => {
  const p = props({
    onb: {
      state: { skipped_steps: ["api-keys"], steps: STEPS },
      complete: vi.fn(),
    } as never,
    summaries: { language: "Deutsch · Auto", "wake-word": "Hey Nova" },
  });
  render(<FinishStep {...p} />);
  const review = screen.getByTestId("onboarding-review");
  expect(review.textContent).toContain("Deutsch · Auto");
  expect(review.textContent).toContain("Hey Nova");
  expect(review.textContent).toContain("onboarding.finish.skipped");
});

it("shows the autostart switch when supported and PUTs on change", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (init?.method === "PUT") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ enabled: true, supported: true }),
      });
    }),
  );
  render(<FinishStep {...props()} />);
  const toggle = await screen.findByTestId("onboarding-autostart");
  expect(toggle.getAttribute("aria-checked")).toBe("true");

  fireEvent.click(toggle);
  await waitFor(() =>
    expect(
      calls.some(
        ([url, init]) =>
          url === "/api/settings/autostart" &&
          init?.method === "PUT" &&
          JSON.parse(init.body as string).enabled === false,
      ),
    ).toBe(true),
  );
});

it("hides the switch when autostart is unsupported (headless) or the probe fails", async () => {
  render(<FinishStep {...props()} />);
  await waitFor(() => expect(screen.queryByTestId("onboarding-autostart")).toBeNull());
  cleanup();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));
  render(<FinishStep {...props()} />);
  await waitFor(() => expect(screen.queryByTestId("onboarding-autostart")).toBeNull());
});

it("the tour is an external link, not an embedded player", () => {
  render(<FinishStep {...props()} />);
  const link = screen.getByRole("link", { name: /tour_title/ }) as HTMLAnchorElement;
  expect(link.href).toContain("youtube.com");
  expect(link.target).toBe("_blank");
  expect(document.querySelector("iframe")).toBeNull();
});

it("with nothing missing there is no disclaimer and Start is live", () => {
  render(<FinishStep {...props()} />);
  expect(screen.queryByTestId("onboarding-gaps")).toBeNull();
  expect(screen.queryByTestId("onboarding-gaps-ack")).toBeNull();
  expect((screen.getByTestId("onboarding-start") as HTMLButtonElement).disabled).toBe(false);
});

it("lists every reported gap and every skipped step, and Start waits for the acknowledgement", () => {
  const p = props({
    onb: {
      state: { skipped_steps: ["wake-word"], steps: STEPS },
      complete: vi.fn(),
    } as never,
    summaries: { language: "Deutsch · Auto", "api-keys": "Gemini + OpenAI · Google Gemini" },
    gaps: { "api-keys": "OpenAI key missing — live voice stays off." },
  });
  render(<FinishStep {...p} />);
  const gaps = screen.getByTestId("onboarding-gaps");
  expect(gaps.textContent).toContain("OpenAI key missing");
  expect(gaps.textContent).toContain("onboarding.finish.gap_skipped_wake_word");
  // A step that reported nothing and was not skipped is not listed.
  expect(gaps.textContent).not.toContain("gap_skipped_language");

  const start = screen.getByTestId("onboarding-start") as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  fireEvent.click(screen.getByTestId("onboarding-gaps-ack"));
  expect(start.disabled).toBe(false);
});
