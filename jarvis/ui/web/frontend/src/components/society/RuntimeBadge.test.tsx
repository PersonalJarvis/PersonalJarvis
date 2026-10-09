import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { RuntimeBadge } from "./RuntimeBadge";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => (key === "society.runtime.runs_on" ? "Runs on {0}" : key) }));

afterEach(cleanup);

describe("RuntimeBadge", () => {
  test.each(["hermes", "openclaw"] as const)("names the %s runtime with its logo", (runtime) => {
    render(<RuntimeBadge runtime={runtime} />);
    const badge = screen.getByRole("img", { name: `Runs on society.runtime.${runtime}` });
    expect(badge.querySelector(`[data-testid="provider-logo-${runtime}"]`)).toBeTruthy();
  });

  test("an agent without a runtime runs on Jarvis", () => {
    render(<RuntimeBadge runtime={undefined} />);
    expect(screen.getByTestId("runtime-badge-jarvis")).toBeTruthy();
    expect(screen.getByTestId("runtime-mark-jarvis")).toBeTruthy();
  });
});
