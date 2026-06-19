import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { FinishStep } from "./FinishStep";
afterEach(cleanup);

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
