import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { OfficeFallback, officeFallbackReason } from "./OfficeFallback";

afterEach(cleanup);

it("offers the equivalent data surface when graphics are unavailable", () => {
  const open = vi.fn();
  render(
    <OfficeFallback
      message="Graphics unavailable"
      actionLabel="Open ledger"
      onAction={open}
    />,
  );

  expect(screen.getByRole("status").textContent).toContain("Graphics unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Open ledger" }));
  expect(open).toHaveBeenCalledOnce();
});


it("uses the static fallback for reduced-motion society maps", () => {
  expect(
    officeFallbackReason({ webgl: true, reduced: true, coding: false, hasLedger: true }),
  ).toBe("reduced-motion");
  expect(
    officeFallbackReason({ webgl: true, reduced: true, coding: true, hasLedger: true }),
  ).toBeNull();
  expect(
    officeFallbackReason({ webgl: true, reduced: true, coding: false, hasLedger: false }),
  ).toBeNull();
  expect(
    officeFallbackReason({ webgl: false, reduced: false, coding: false, hasLedger: true }),
  ).toBe("graphics");
});
