import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { FinishStep } from "./FinishStep";

// Default stub: capability probe answers "unsupported" so the legacy tests
// exercise the step without the autostart toggle.
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

it("calls goNext (= complete) on the start CTA", () => {
  const goNext = vi.fn();
  render(<FinishStep onb={{ state: { skipped_steps: [] } } as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast />);
  fireEvent.click(screen.getByRole("button", { name: "onboarding.finish.start_cta" }));
  expect(goNext).toHaveBeenCalled();
});

it("lists skipped steps", () => {
  render(<FinishStep onb={{ state: { skipped_steps: ["api-keys", "mic-test"] } } as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast />);
  expect(screen.getByText("api-keys")).toBeDefined();
  expect(screen.getByText("mic-test")).toBeDefined();
});

const stepProps = {
  onb: { state: { skipped_steps: [] } } as never,
  goNext: vi.fn(),
  goBack: vi.fn(),
  skip: vi.fn(),
  isFirst: false,
  isLast: true,
};

it("shows the autostart toggle when supported and PUTs on change", async () => {
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
  render(<FinishStep {...stepProps} />);
  const toggle = (await screen.findByRole("checkbox")) as HTMLInputElement;
  expect(toggle.checked).toBe(true);

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

it("hides the toggle when autostart is unsupported (headless)", async () => {
  render(<FinishStep {...stepProps} />);
  await waitFor(() => expect(screen.queryByRole("checkbox")).toBeNull());
});

it("hides the toggle when the capability probe fails (fail quiet)", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));
  render(<FinishStep {...stepProps} />);
  await waitFor(() => expect(screen.queryByRole("checkbox")).toBeNull());
});
